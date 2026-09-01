// Databricks notebook source
// MAGIC %md
// MAGIC # 01 · Embudo de onboarding — EDA base (Scala)
// MAGIC
// MAGIC **Equipo 1 · Frente C** — Business Analysts
// MAGIC
// MAGIC Entregable: **tabla de conversión etapa por etapa con el mayor punto de abandono cuantificado**.
// MAGIC
// MAGIC | # | Paso | Por qué |
// MAGIC |---|------|---------|
// MAGIC | 1 | Carga y esquema | Confirmar volúmenes contra el reto |
// MAGIC | 2 | Diccionario de datos | El mapa para los otros tres BAs |
// MAGIC | 3 | Filas por cliente | 1 fila ≠ 1 persona (reintentos de selfie) |
// MAGIC | 4 | Prevalencia del target | Debe dar 19.2% |
// MAGIC | 5 | Barrido de leakage | El reto esconde al menos una columna |
// MAGIC | 6 | Dedup de reintentos | Sin esto todo conteo está inflado |
// MAGIC | 7 | Tabla del embudo | El entregable |
// MAGIC
// MAGIC **Compuerta:** no graficar nada hasta que la conversión acumulada reproduzca el **44.3%**.
// MAGIC
// MAGIC Todo con DataFrame API — sin SQL.

// COMMAND ----------

import org.apache.spark.sql.{DataFrame, Row}
import org.apache.spark.sql.functions._
import org.apache.spark.sql.expressions.Window
import spark.implicits._

val DATA = "/Volumes/usr/eduardo_ortiz/hackaton"

// Los parquet fueron escritos por pandas con timestamps en NANOsegundos y Spark
// solo soporta micros -> "Illegal Parquet type: INT64 (TIMESTAMP(NANOS,false))".
// Con esto los timestamps se leen como bigint (nanos desde epoch), que es
// suficiente para ordenar y para min/max. Para verlos como fecha:
//   from_unixtime(col("event_ts") / 1000000000L)
spark.conf.set("spark.sql.legacy.parquet.nanosAsLong", "true")

// Baselines publicados en el reto — nuestras pruebas de sanidad
val BASELINE_ONBOARDING = 0.443
val BASELINE_ACTIVACION = 0.192

val SEED = 42L

// COMMAND ----------

// MAGIC %md
// MAGIC ## 1 · Carga y esquema
// MAGIC Esperado: customers 260,000 · onboarding_events 1,386,264 · activation_labels 260,000

// COMMAND ----------

val customers = spark.read.parquet(s"$DATA/customers.parquet").cache()
val events    = spark.read.parquet(s"$DATA/onboarding_events.parquet").cache()
val labels    = spark.read.parquet(s"$DATA/activation_labels.parquet").cache()

val nCust = customers.count()
val nEv   = events.count()
val nLab  = labels.count()

println(f"customers          $nCust%,d filas x ${customers.columns.length} cols")
println(f"onboarding_events  $nEv%,d filas x ${events.columns.length} cols")
println(f"activation_labels  $nLab%,d filas x ${labels.columns.length} cols")
println()
println("customers:         " + customers.columns.mkString(", "))
println("onboarding_events: " + events.columns.mkString(", "))
println("activation_labels: " + labels.columns.mkString(", "))

// COMMAND ----------

// MAGIC %md
// MAGIC ### Esquema esperado
// MAGIC ```
// MAGIC customers          customer_id, signup_ts, acquisition_channel, device_os,
// MAGIC                    app_version, age, state, prev_bank_relationship,
// MAGIC                    referred_by_customer
// MAGIC onboarding_events  customer_id, event_id, event_ts, step_number, step_name,
// MAGIC                    status, attempt_no, ms_on_step, device_os, app_version
// MAGIC activation_labels  customer_id, completed_onboarding, steps_completed,
// MAGIC                    activated_30d, days_to_first_transaction, first_transaction_ts
// MAGIC ```
// MAGIC Si algún nombre difiere, ajustar las constantes de la celda siguiente y nada más.

// COMMAND ----------

// Nombres de columna en un solo lugar. Si el dataset cambia, se toca aquí.
val KEY        = "customer_id"
val C_ETAPA    = "step_name"
val C_ETAPA_N  = "step_number"
val C_ESTADO   = "status"
val C_INTENTO  = "attempt_no"
val C_TIEMPO   = "event_ts"
val C_DURACION = "ms_on_step"
val C_TARGET   = "activated_30d"

// Sospechosas de fuga de target: todas se conocen DESPUES del desenlace
val LEAK_SOSPECHOSAS = Seq(
  "days_to_first_transaction",  // funcion directa del target
  "first_transaction_ts",       // existe solo si hubo transaccion
  "steps_completed",            // resultado del embudo, no antecedente
  "completed_onboarding"        // resultado del embudo, no antecedente
)

// COMMAND ----------

// MAGIC %md
// MAGIC ## 2 · Diccionario de datos
// MAGIC Los nulos son **intencionales**: un nulo que solo aparece en quien abandonó es información, no un hueco. Imputar a la media la destruye.

// COMMAND ----------

// Una sola pasada por tabla: nulos y cardinalidad aproximada de cada columna.
def diccionario(df: DataFrame, tabla: String): DataFrame = {
  val total = df.count()

  val aggs = df.columns.flatMap { c =>
    Seq(
      sum(when(col(c).isNull, lit(1)).otherwise(lit(0))).alias(s"n__$c"),
      approx_count_distinct(col(c)).alias(s"c__$c")
    )
  }

  val fila: Row = df.agg(aggs.head, aggs.tail: _*).head()

  val filas = df.schema.fields.map { f =>
    val nulos = fila.getAs[Long](s"n__${f.name}")
    val card  = fila.getAs[Long](s"c__${f.name}")
    val pct   = if (total == 0) 0.0 else BigDecimal(100.0 * nulos / total)
                  .setScale(2, BigDecimal.RoundingMode.HALF_UP).toDouble
    (tabla, f.name, f.dataType.simpleString, nulos, pct, card)
  }

  filas.toSeq.toDF("tabla", "columna", "tipo", "n_nulos", "pct_nulos", "cardinalidad")
}

val dic = diccionario(customers, "customers")
  .union(diccionario(events, "onboarding_events"))
  .union(diccionario(labels, "activation_labels"))

display(dic)

// COMMAND ----------

println("Columnas con nulos intencionales (cada una necesita una decision documentada):\n")
dic.filter($"n_nulos" > 0)
   .orderBy($"pct_nulos".desc)
   .collect()
   .foreach(r => println(f"  ${r.getString(0)}.${r.getString(1)}%-30s ${r.getDouble(4)}%6.2f%% nulo"))

// COMMAND ----------

// MAGIC %md
// MAGIC ## 3 · Filas por cliente — la trampa de los reintentos
// MAGIC 1,386,264 eventos sobre 260,000 personas ≈ **5.3 filas por cliente**. La selfie permite varios intentos: contar filas como personas infla el embudo entero.

// COMMAND ----------

val clientesEnEventos = events.select(col(KEY)).distinct().count()
println(f"filas: $nEv%,d   clientes unicos: $clientesEnEventos%,d   ratio: ${nEv.toDouble / clientesEnEventos}%.2f")

// COMMAND ----------

// Donde se concentran los reintentos
val porEtapa = events
  .groupBy(col(C_ETAPA))
  .agg(
    count(lit(1)).alias("eventos"),
    countDistinct(col(KEY)).alias("clientes"),
    min(col(C_ETAPA_N)).alias("orden")
  )
  .withColumn("eventos_por_cliente", round($"eventos" / $"clientes", 2))
  .orderBy($"eventos_por_cliente".desc)

display(porEtapa)

// COMMAND ----------

// MAGIC %md
// MAGIC ## 4 · Prevalencia del target
// MAGIC Prueba de sanidad contra el baseline del reto: **19.2%**.

// COMMAND ----------

val statsTarget = labels
  .agg(
    sum(col(C_TARGET).cast("double")).alias("positivos"),
    count(lit(1)).alias("total")
  )
  .head()

val positivos = statsTarget.getAs[Double]("positivos")
val totalLab  = statsTarget.getAs[Long]("total").toDouble
val tasa      = positivos / totalLab

println(f"target: $C_TARGET")
println(f"positivos: ${positivos.toLong}%,d de ${totalLab.toLong}%,d  ->  ${tasa * 100}%.2f%%")
println(f"baseline del reto: ${BASELINE_ACTIVACION * 100}%.1f%%   desvio: ${math.abs(tasa - BASELINE_ACTIVACION) * 100}%.2f pp")
println()
println(f"desbalance: 1 : ${(1 - tasa) / tasa}%.2f")
println(f"PR-AUC de un modelo aleatorio (el piso a superar): $tasa%.4f")
println(f"accuracy de predecir 'nadie se activa': ${(1 - tasa) * 100}%.2f%%  <- por esto accuracy no sirve")

// COMMAND ----------

// MAGIC %md
// MAGIC ## 5 · Barrido de fuga de target (leakage)
// MAGIC
// MAGIC El reto avisa que hay al menos una columna con leak. Criterio: si una sola columna da AUC > 0.95, es leak, no es talento.
// MAGIC
// MAGIC Cada columna se prueba de dos formas, y la segunda es la que caza el leak de verdad:
// MAGIC
// MAGIC - **`valor`** — ¿el contenido predice el target?
// MAGIC - **`nulidad`** — ¿el *hecho de estar nula* predice el target? `days_to_first_transaction` solo tiene valor para quien se activó: sobre los datos presentes el AUC es indefinido y el leak pasaría desapercibido. **La ausencia del dato es el target.**

// COMMAND ----------

// AUC de una sola columna via Mann-Whitney sobre rangos.
// Devuelve el poder predictivo (simetrico): nos importa la magnitud, no el signo.
def aucUnivariada(df: DataFrame, feature: String, target: String): Option[Double] = {
  val d = df
    .select(col(feature).cast("double").alias("x"), col(target).cast("double").alias("y"))
    .filter(col("x").isNotNull && col("y").isNotNull)

  val res = d.agg(
    count(lit(1)).alias("n"),
    sum(col("y")).alias("n1")
  ).head()

  val n  = res.getAs[Long]("n")
  val n1 = Option(res.getAs[java.lang.Double]("n1")).map(_.toDouble).getOrElse(0.0)
  val n0 = n - n1

  if (n < 100 || n1 <= 0 || n0 <= 0) return None

  // Rango MEDIO en empates. rank() solo da el rango minimo, y con features
  // binarias o de baja cardinalidad eso rompe el AUC (llega a dar > 1).
  val wOrden = Window.orderBy(col("x"))
  val wValor = Window.partitionBy(col("x"))
  val conRango = d
    .withColumn("rmin", rank().over(wOrden).cast("double"))
    .withColumn("cnt",  count(lit(1)).over(wValor).cast("double"))
    .withColumn("r",    col("rmin") + (col("cnt") - lit(1.0)) / lit(2.0))
  val sumaRangosPositivos = conRango
    .filter(col("y") === 1.0)
    .agg(sum(col("r")).alias("s"))
    .head()
    .getAs[Double]("s")

  val auc = (sumaRangosPositivos - n1 * (n1 + 1) / 2) / (n1 * n0)
  Some(math.max(auc, 1 - auc))
}

// COMMAND ----------

// Base para el scan: labels + customers unidos por customer_id
val base = labels.join(customers, Seq(KEY), "left").cache()

val candidatas = base.schema.fields
  .filter(f => f.name != C_TARGET && f.name != KEY)
  .filter { f =>
    f.dataType.simpleString match {
      case "string" | "boolean" => true                      // se castean o se prueban por nulidad
      case t if t.startsWith("decimal") => true
      case "int" | "bigint" | "double" | "float" | "smallint" | "tinyint" | "date" | "timestamp" => true
      case _ => false
    }
  }
  .map(_.name)

case class Hallazgo(columna: String, forma: String, auc: Double, veredicto: String)

def veredictoDe(auc: Double): String =
  if (auc >= 0.95) "LEAK" else if (auc >= 0.80) "sospechosa" else "feature plausible"

val hallazgos = candidatas.flatMap { c =>
  val porValor = aucUnivariada(base, c, C_TARGET)
    .map(a => Hallazgo(c, "valor", a, veredictoDe(a)))

  val tieneNulos = base.filter(col(c).isNull).limit(1).count() > 0
  val tieneDatos = base.filter(col(c).isNotNull).limit(1).count() > 0

  val porNulidad =
    if (tieneNulos && tieneDatos) {
      val conInd = base.withColumn("__es_nulo", when(col(c).isNull, lit(1.0)).otherwise(lit(0.0)))
      aucUnivariada(conInd, "__es_nulo", C_TARGET)
        .map(a => Hallazgo(s"$c [es_nulo]", "nulidad", a, veredictoDe(a)))
    } else None

  Seq(porValor, porNulidad).flatten
}

val leakDF = hallazgos.toSeq.toDF().orderBy($"auc".desc)
display(leakDF)

// COMMAND ----------

val graves = hallazgos.filter(_.auc >= 0.95)

println(">>> EXCLUIR Y DOCUMENTAR EN /docs/decision-log.md:\n")
if (graves.nonEmpty) graves.foreach(h => println(f"  - ${h.columna}%-40s AUC ${h.auc}%.4f  (via ${h.forma})"))
else println("  Nada por encima de 0.95 automaticamente.")

println("\nRevisar igual a mano — el criterio es temporal, no estadistico:")
LEAK_SOSPECHOSAS.foreach(c => println(s"  - $c"))

// Lista que consume David para el modelo. Se excluye la columna completa,
// no solo la forma que salio marcada.
val COLUMNAS_EXCLUIDAS = (graves.map(_.columna.replace(" [es_nulo]", "")) ++ LEAK_SOSPECHOSAS).distinct.sorted
println("\nCOLUMNAS_EXCLUIDAS = " + COLUMNAS_EXCLUIDAS.mkString(", "))

// COMMAND ----------

// MAGIC %md
// MAGIC ## 6 · Dedup de reintentos
// MAGIC Una fila por `customer_id × step_name`, con el **estado final** de la etapa. El conteo de intentos se guarda como feature: no se tira, es de las mejores señales del modelo.

// COMMAND ----------

// Primero: que valores de status existen realmente
display(events.groupBy(col(C_ESTADO)).agg(count(lit(1)).alias("n")).orderBy($"n".desc))

// COMMAND ----------

// Ajustar a los valores reales que salieron arriba.
// OJO: "retry" NO es exito (es un intento fallido intermedio) y "abandoned" tampoco.
val ESTADOS_EXITO = Seq("success", "completed", "complete", "approved", "ok", "passed", "done")

val wCliEtapa = Window.partitionBy(col(KEY), col(C_ETAPA))
val wFinal    = wCliEtapa.orderBy(col(C_INTENTO).desc, col(C_TIEMPO).desc)

val vista = events
  .withColumn("rn",          row_number().over(wFinal))
  .withColumn("n_intentos",  count(lit(1)).over(wCliEtapa))
  .withColumn("intento_max", max(col(C_INTENTO)).over(wCliEtapa))
  .withColumn("t_primero",   min(col(C_TIEMPO)).over(wCliEtapa))
  .withColumn("t_ultimo",    max(col(C_TIEMPO)).over(wCliEtapa))
  .withColumn("ms_total",    sum(col(C_DURACION)).over(wCliEtapa))
  .filter(col("rn") === 1)
  .drop("rn")
  .withColumnRenamed(C_ESTADO, "estado_final")
  .withColumn("exito", lower(col("estado_final")).isin(ESTADOS_EXITO.map(_.toLowerCase): _*))
  .cache()

val filasVista       = vista.count()
val clientesEnVista  = vista.select(col(KEY)).distinct().count()
val duplicadosEtapa  = vista.groupBy(col(KEY), col(C_ETAPA)).agg(count(lit(1)).alias("n")).filter($"n" > 1).count()

println(f"vista deduplicada: $filasVista%,d filas")
println(f"clientes en vista:      $clientesEnVista%,d")
println(f"clientes en customers:  $nCust%,d")
println(f"filas duplicadas por (cliente, etapa): $duplicadosEtapa")

require(clientesEnVista == nCust, "Los clientes de la vista no cuadran con customers. Parar y revisar el dedup.")
println("\n[OK] El dedup cuadra.")

// COMMAND ----------

display(vista.limit(20))

// COMMAND ----------

// MAGIC %md
// MAGIC ## 7 · Tabla del embudo — el entregable
// MAGIC Las 7 etapas se ordenan por `step_number`, que viene explícito en el dato.

// COMMAND ----------

val porOrden = Window.orderBy($"orden")

val crudo = vista
  .groupBy(col(C_ETAPA))
  .agg(
    min(col(C_ETAPA_N)).alias("orden"),
    countDistinct(col(KEY)).alias("llegan"),
    countDistinct(when(col("exito"), col(KEY))).alias("pasan")
  )

val entran = crudo.orderBy($"orden").head().getAs[Long]("llegan").toDouble
val fugaTotal = crudo.agg(sum($"llegan" - $"pasan").alias("f")).head().getAs[Long]("f").toDouble

val embudo = crudo
  .withColumn("pierden", $"llegan" - $"pasan")
  .withColumn("conv_etapa",     round($"pasan" / $"llegan" * 100, 2))
  .withColumn("conv_acumulada", round($"pasan" / lit(entran) * 100, 2))
  .withColumn("pct_de_la_fuga", round(($"llegan" - $"pasan") / lit(fugaTotal) * 100, 2))
  .orderBy($"orden")

display(embudo)

// COMMAND ----------

// Veredicto: la frase que Product va a citar en el pitch
val filas = embudo.collect()

val peor = filas.maxBy(_.getAs[Long]("pierden"))
val convFinal = filas.last.getAs[Double]("conv_acumulada") / 100.0
val desvio = math.abs(convFinal - BASELINE_ONBOARDING)

println(f"Etapa critica: ${peor.getAs[String](C_ETAPA)} — ${peor.getAs[Long]("pierden")}%,d clientes perdidos " +
        f"(${peor.getAs[Double]("pct_de_la_fuga")}%.1f%% de toda la fuga del embudo).")
println(f"Conversion completa del onboarding: ${convFinal * 100}%.3f%%  (baseline del reto: ${BASELINE_ONBOARDING * 100}%.1f%%)")

if (desvio > 0.02)
  println(f"\n[!] Desvio de ${desvio * 100}%.1f pp contra el baseline. NO graficar todavia. Revisar, en orden:\n" +
          "    1. ESTADOS_EXITO no cubre los valores reales de status\n" +
          "    2. el orden de las etapas (step_number) no es el del embudo real\n" +
          "    3. el dedup se quedo con el intento equivocado")
else
  println("\n[OK] Cuadra con el baseline. El embudo es confiable — seguir a las graficas.")

// COMMAND ----------

// MAGIC %md
// MAGIC ### Compuerta
// MAGIC Si la celda de arriba marcó desvío, **parar aquí**. Todo lo que se grafique arriba de un embudo mal deduplicado se tira.

// COMMAND ----------

// Grafica: usar el boton de visualizacion del display() sobre esta tabla.
// Recomendado: barras horizontales de `pierden` por etapa, ordenadas por `orden`.
display(embudo.select(col(C_ETAPA), $"orden", $"llegan", $"pasan", $"pierden", $"pct_de_la_fuga"))

// COMMAND ----------

// MAGIC %md
// MAGIC ## 8 · Handoff
// MAGIC
// MAGIC | Salida | Para quién |
// MAGIC |--------|-----------|
// MAGIC | `usr.hackaton_equipo1.embudo_etapas` | Product — prioriza la etapa crítica |
// MAGIC | `usr.hackaton_equipo1.vista_etapas` | segmentos y features |
// MAGIC | `usr.hackaton_equipo1.leakage_scan` | modelo — columnas a excluir |
// MAGIC | `usr.hackaton_equipo1.diccionario_datos` | todo el equipo |

// COMMAND ----------

val SCHEMA_SALIDA = "usr.hackaton_equipo1"

embudo.write.mode("overwrite").saveAsTable(s"$SCHEMA_SALIDA.embudo_etapas")
vista.write.mode("overwrite").saveAsTable(s"$SCHEMA_SALIDA.vista_etapas")
leakDF.write.mode("overwrite").saveAsTable(s"$SCHEMA_SALIDA.leakage_scan")
dic.write.mode("overwrite").saveAsTable(s"$SCHEMA_SALIDA.diccionario_datos")

println(s"Guardado en $SCHEMA_SALIDA:")
println("  embudo_etapas, vista_etapas, leakage_scan, diccionario_datos")
println("\nSiguiente: Carlos arranca 02_segmentos leyendo vista_etapas + customers.")