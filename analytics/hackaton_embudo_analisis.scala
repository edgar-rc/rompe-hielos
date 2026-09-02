// Databricks notebook source
// MAGIC %md
// MAGIC # Hackaton · Analisis
// MAGIC ### Equipo 1 · 

// COMMAND ----------

// MAGIC %md
// MAGIC ## 1 · Datos
// MAGIC
// MAGIC Tres archivos parquet
// MAGIC
// MAGIC El `spark.conf.set` de abajo es necesario porque los parquet fueron escritos con
// MAGIC timestamps en nanosegundos y Spark solo soporta microsegundos. Sin esa linea,
// MAGIC la lectura falla con `Illegal Parquet type: INT64 (TIMESTAMP(NANOS,false))`.

// COMMAND ----------

import org.apache.spark.sql.functions._

spark.conf.set("spark.sql.legacy.parquet.nanosAsLong", "true")

val RUTA = "/Volumes/usr/eduardo_ortiz/hackaton"

val customers = spark.read.parquet(s"$RUTA/customers.parquet")
val events    = spark.read.parquet(s"$RUTA/onboarding_events.parquet")
val labels    = spark.read.parquet(s"$RUTA/activation_labels.parquet")

println(f"customers          ${customers.count()}%,d filas")
println(f"onboarding_events  ${events.count()}%,d filas")
println(f"activation_labels  ${labels.count()}%,d filas")

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos:** 260,000 personas, 260,000 etiquetas y 1,386,264 eventos.
// MAGIC
// MAGIC Los primeros dos numeros son iguales, asi que cada persona tiene exactamente una
// MAGIC etiqueta. El tercero es 5.3 veces mas grande: la tabla de eventos tiene varias
// MAGIC filas por persona. Eso es lo esperado — es un log de pasos — pero es la primera
// MAGIC cosa con la que hay que tener cuidado.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 2 · Que hay en cada tabla
// MAGIC
// MAGIC Antes de calcular nada, mirar las columnas.

// COMMAND ----------

println("=== customers ===")
customers.printSchema()

println("=== onboarding_events ===")
events.printSchema()

println("=== activation_labels ===")
labels.printSchema()

// COMMAND ----------

display(events.limit(10))

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos:**
// MAGIC
// MAGIC - `customers` — quien es la persona: canal de adquisicion, sistema operativo, version de la app, edad, estado, si ya era cliente de otro banco, si fue referida.
// MAGIC - `onboarding_events` — un registro por intento de etapa: que etapa (`step_name`, `step_number`), si paso (`status`), **que numero de intento fue (`attempt_no`)** y cuanto tardo (`ms_on_step`).
// MAGIC - `activation_labels` — el resultado: si completo el onboarding, cuantas etapas hizo, y **`activated_30d`, que es lo que queremos entender**.
// MAGIC
// MAGIC La columna `attempt_no` es la que confirma que puede haber varios intentos por
// MAGIC etapa. Eso es el tema de la siguiente seccion.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 3 · La trampa: una fila no es una persona
// MAGIC
// MAGIC Si contamos filas en lugar de personas, cualquier etapa que permita reintentos
// MAGIC se ve inflada. Veamos si eso realmente pasa.

// COMMAND ----------

val intentosPorEtapa = events
  .groupBy(col("step_number"), col("step_name"))
  .agg(
    count(lit(1)).alias("filas"),
    countDistinct(col("customer_id")).alias("personas"),
    max(col("attempt_no")).alias("max_intentos")
  )
  .withColumn("filas_por_persona", round(col("filas") / col("personas"), 3))
  .orderBy(col("step_number"))

display(intentosPorEtapa)

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos:** en seis de las siete etapas hay exactamente 1 fila por persona.
// MAGIC En `selfie_liveness` hay mas de una, y `max_intentos` es mayor que 1.
// MAGIC
// MAGIC O sea: **la selfie es la unica etapa del onboarding que permite reintentar.**
// MAGIC
// MAGIC Esto tiene dos consecuencias. La primera, tecnica: si contamos filas, la selfie
// MAGIC parece tener mas trafico del que tiene, y cualquier conclusion sobre ella queda
// MAGIC mal. La segunda, de producto: es la unica pantalla donde la gente puede fallar
// MAGIC y volver a intentar, lo que ya sugiere que ahi falla algo.
// MAGIC
// MAGIC Antes de seguir, hay que colapsar los reintentos.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 4 · La vista limpia: una fila por persona y etapa
// MAGIC
// MAGIC Agrupamos por persona y etapa. De todos los intentos de esa persona en esa
// MAGIC etapa nos quedamos con: cuantos intentos hizo, si logro pasar alguna vez, y
// MAGIC el tiempo total que le dedico.

// COMMAND ----------

val vista = events
  .groupBy(col("customer_id"), col("step_name"))
  .agg(
    min(col("step_number")).alias("step_number"),
    count(lit(1)).alias("n_intentos"),
    max(when(lower(col("status")) === "completed", 1).otherwise(0)).alias("paso"),
    sum(col("ms_on_step")).alias("ms_total")
  )

val filas     = vista.count()
val personas  = vista.select(col("customer_id")).distinct().count()

println(f"Filas en la vista limpia: $filas%,d   (antes: ${events.count()}%,d)")
println(f"Personas distintas:       $personas%,d   (en customers: ${customers.count()}%,d)")

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos:** 1,284,599 filas, 260,000 personas.
// MAGIC
// MAGIC Dos verificaciones importantes aqui:
// MAGIC
// MAGIC 1. Las personas de la vista cuadran exactamente con `customers` (260,000). No
// MAGIC    perdimos ni inventamos a nadie al agrupar.
// MAGIC 2. Se fueron ~102,000 filas, que eran los reintentos de la selfie.
// MAGIC
// MAGIC Un detalle que vale aclarar: 1,284,599 / 260,000 = 4.9 etapas por persona.
// MAGIC No es que la gente reintente cinco veces; es que **la mayoria no llega a las
// MAGIC siete etapas porque se cae en el camino.** Ese es justo el problema que vamos
// MAGIC a medir.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 5 · El embudo
// MAGIC
// MAGIC Ahora si. Por cada etapa: cuanta gente llega y cuanta pasa.

// COMMAND ----------

val embudo = vista
  .groupBy(col("step_number"), col("step_name"))
  .agg(
    countDistinct(col("customer_id")).alias("llegan"),
    sum(col("paso")).alias("pasan")
  )
  .withColumn("se_caen", col("llegan") - col("pasan"))
  .withColumn("conversion_pct", round(col("pasan") / col("llegan") * 100, 2))
  .withColumn("acumulada_pct", round(col("pasan") / lit(260000.0) * 100, 2))
  .orderBy(col("step_number"))

display(embudo)

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos:** la conversion acumulada final es **44.3%**, identica al
// MAGIC baseline publicado del reto.
// MAGIC
// MAGIC Eso es la validacion mas importante del notebook. Si nuestro numero no cuadrara
// MAGIC con el baseline, significaria que el dedup o el criterio de "paso" estan mal, y
// MAGIC todo lo de abajo seria basura. Cuadra, asi que podemos seguir.
// MAGIC
// MAGIC De 260,000 que empiezan, **115,192 terminan el onboarding**.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 6 · Donde esta la fuga
// MAGIC
// MAGIC Saber que se cae el 56% no dice donde actuar. Lo que importa es como se
// MAGIC reparte esa perdida entre las siete etapas.

// COMMAND ----------

val fugaTotal = 260000.0 - 115192.0   // 144,808 personas perdidas en el embudo

val dondeSeCae = embudo
  .select(col("step_number"), col("step_name"), col("se_caen"))
  .withColumn("pct_de_toda_la_fuga", round(col("se_caen") / lit(fugaTotal) * 100, 1))
  .orderBy(col("se_caen").desc)

display(dondeSeCae)

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos, y esto es el hallazgo principal:**
// MAGIC
// MAGIC | Etapa | Se caen | % de toda la fuga |
// MAGIC |---|---|---|
// MAGIC | selfie_liveness | 65,129 | **45.0%** |
// MAGIC | id_document_upload | 36,629 | 25.3% |
// MAGIC | personal_data | 20,038 | 13.8% |
// MAGIC | phone_verification | 12,691 | 8.8% |
// MAGIC | address | 6,841 | 4.7% |
// MAGIC | terms_acceptance | 3,480 | 2.4% |
// MAGIC | account_created | 0 | 0% |
// MAGIC
// MAGIC La selfie sola concentra **45% de toda la fuga del embudo** — tres veces mas que
// MAGIC la siguiente etapa. Y junto con la subida de documento suman **70%**.
// MAGIC
// MAGIC Las dos son pasos de verificacion de identidad. Y despues de la selfie el embudo
// MAGIC casi no pierde a nadie: 94.6%, 97.1% y 100% de conversion.
// MAGIC
// MAGIC **No hay siete problemas. Hay uno.**

// COMMAND ----------

// MAGIC %md
// MAGIC ## 7 · Que tan distintas son esas dos etapas
// MAGIC
// MAGIC Ya sabemos donde se cae la gente. Ahora: por que? Miramos cuanto tarda cada
// MAGIC etapa y cuanta gente tiene que reintentarla.

// COMMAND ----------

val esfuerzo = vista
  .groupBy(col("step_number"), col("step_name"))
  .agg(
    round(percentile_approx(col("ms_total"), lit(0.5), lit(10000)) / 1000.0, 1).alias("segundos_mediana"),
    round(percentile_approx(col("ms_total"), lit(0.95), lit(10000)) / 1000.0, 1).alias("segundos_p95"),
    round(avg(col("n_intentos")), 3).alias("intentos_promedio"),
    round(avg(when(col("n_intentos") > 1, 1.0).otherwise(0.0)) * 100, 1).alias("pct_reintenta")
  )
  .orderBy(col("step_number"))

display(esfuerzo)

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos: son dos problemas distintos, no uno.**
// MAGIC
// MAGIC **La selfie falla.** 40.2% de quienes llegan tienen que intentarlo mas de una vez.
// MAGIC Es la unica etapa con reintentos. Y no es que sea tardada: 49 segundos de mediana,
// MAGIC mas rapida que subir el documento. Simplemente **no pasa**. Aun con reintentos
// MAGIC permitidos, un 34% acaba abandonando.
// MAGIC
// MAGIC **Subir el documento es tardado.** 134 segundos de mediana y un p95 de casi 8
// MAGIC minutos: el paso mas lento del embudo. Y no permite reintentar — si falla, se
// MAGIC acabo. Ahi se caen 36,629.
// MAGIC
// MAGIC La conclusion practica: si Product ataca "verificacion de identidad" como si
// MAGIC fuera un solo problema, va a resolver mal uno de los dos. La selfie necesita
// MAGIC que el chequeo sea mas permisivo o de mejor feedback. El documento necesita que
// MAGIC sea mas rapido.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 8 · El segundo embudo, que nadie estaba mirando
// MAGIC
// MAGIC Completar el onboarding no es el objetivo. El objetivo es `activated_30d`:
// MAGIC que la persona haga su primera transaccion en 30 dias.
// MAGIC
// MAGIC Cruzamos las dos cosas.

// COMMAND ----------

val segundoEmbudo = labels
  .groupBy(col("completed_onboarding"))
  .agg(
    count(lit(1)).alias("personas"),
    sum(col("activated_30d").cast("int")).alias("se_activan")
  )
  .withColumn("nunca_transaccionan", col("personas") - col("se_activan"))
  .withColumn("pct_activacion", round(col("se_activan") / col("personas") * 100, 1))
  .orderBy(col("completed_onboarding").desc)

display(segundoEmbudo)

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos, y es mas grave de lo que parecia:**
// MAGIC
// MAGIC | Completo el onboarding | Personas | Se activan | Nunca transaccionan | % activacion |
// MAGIC |---|---|---|---|---|
// MAGIC | si | 115,192 | 50,024 | 65,168 | **43.4%** |
// MAGIC | no | 144,808 | **0** | 144,808 | **0%** |
// MAGIC
// MAGIC Dos lecturas:
// MAGIC
// MAGIC **1. Nadie que no complete el onboarding se activa. Cero. Ni una persona.** La
// MAGIC cuenta no existe, asi que no hay forma de transaccionar. Eso confirma que el
// MAGIC embudo es un cuello de botella real y no una metrica paralela.
// MAGIC
// MAGIC **2. De los que SI abren la cuenta, el 56.6% nunca la usa.** Son 65,168 clientes
// MAGIC perdidos *despues* del onboarding, contra 144,808 perdidos dentro de el. Dos
// MAGIC fugas de tamano comparable.
// MAGIC
// MAGIC Y aqui esta lo importante para el pitch: **las dos fugas se multiplican, no se
// MAGIC suman.** Si arreglamos la selfie y rescatamos 10,000 personas, solo unas 4,340
// MAGIC van a hacer una transaccion. Cualquier proyeccion de impacto que ignore eso
// MAGIC infla el resultado mas del doble.
// MAGIC
// MAGIC Verificacion: 50,024 / 260,000 = 19.2%, que es exactamente el otro baseline del
// MAGIC reto. Los dos numeros publicados quedan reproducidos.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 9 · Una advertencia para quien vaya a modelar
// MAGIC
// MAGIC Hay columnas en `activation_labels` que no se pueden usar como features, porque
// MAGIC solo existen *despues* del resultado que queremos predecir. Lo demostramos.

// COMMAND ----------

val fuga = labels
  .groupBy(
    col("activated_30d"),
    col("days_to_first_transaction").isNull.alias("dias_esta_vacio"),
    col("first_transaction_ts").isNull.alias("fecha_esta_vacia")
  )
  .agg(count(lit(1)).alias("personas"))
  .orderBy(col("activated_30d").desc)

display(fuga)

// COMMAND ----------

// MAGIC %md
// MAGIC **Lo que vemos:** las dos columnas estan vacias **exactamente** cuando
// MAGIC `activated_30d = false`, y llenas exactamente cuando es `true`. No hay ni un
// MAGIC caso mezclado.
// MAGIC
// MAGIC O sea que el simple hecho de que la columna este vacia ya revela la respuesta.
// MAGIC Un modelo que las use daria 100% de acierto en el entrenamiento y 0% de valor
// MAGIC en la realidad.
// MAGIC
// MAGIC Y ojo con el detalle: **lo delator no es el valor, es la ausencia.** Un chequeo
// MAGIC de leakage que solo mire los valores presentes no lo detecta, porque al filtrar
// MAGIC los nulos se queda con puros casos positivos.
// MAGIC
// MAGIC Columnas a excluir del modelo:
// MAGIC `days_to_first_transaction`, `first_transaction_ts`, `completed_onboarding`,
// MAGIC `steps_completed`.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 10 · Resumen
// MAGIC
// MAGIC | | |
// MAGIC |---|---|
// MAGIC | Empiezan el registro | 260,000 |
// MAGIC | Terminan el onboarding | 115,192 · **44.3%** |
// MAGIC | Transaccionan en 30 dias | 50,024 · **19.2%** |
// MAGIC
// MAGIC **Los cuatro hallazgos:**
// MAGIC
// MAGIC 1. La **selfie** concentra 65,129 abandonos = **45% de toda la fuga**, tres veces la siguiente etapa.
// MAGIC 2. Junto con la **subida de documento** son **70%**. Las dos son verificacion de identidad. Despues de la selfie el embudo casi no pierde a nadie.
// MAGIC 3. Son dos fricciones distintas: la selfie **falla** (unica etapa con reintentos, 40%), el documento es **lento** (p95 de 8 minutos, sin reintentos).
// MAGIC 4. De los que completan el onboarding, **56.6% nunca transacciona**. Y de los que no lo completan, se activa **cero**. Las dos fugas se multiplican.
// MAGIC
// MAGIC **Advertencias metodologicas:**
// MAGIC
// MAGIC - Una fila de eventos no es una persona: solo la selfie permite reintentos. Todo aqui usa la vista deduplicada.
// MAGIC - Hay fuga de target en las etiquetas, detectable solo por el patron de nulos.
// MAGIC - Con 19.2% de positivos, un modelo que prediga "nadie se activa" acierta 80.8%. Por eso no usamos accuracy.

// COMMAND ----------

// MAGIC %md
// MAGIC ## 11 · Tablas guardadas
// MAGIC
// MAGIC Para que el resto del equipo trabaje sobre lo mismo.

// COMMAND ----------

val SCHEMA = "usr.hackaton_equipo1"

vista.write.mode("overwrite").saveAsTable(s"$SCHEMA.analisis_vista")
embudo.write.mode("overwrite").saveAsTable(s"$SCHEMA.analisis_embudo")
esfuerzo.write.mode("overwrite").saveAsTable(s"$SCHEMA.analisis_esfuerzo")
segundoEmbudo.write.mode("overwrite").saveAsTable(s"$SCHEMA.analisis_segundo_embudo")

println(s"Guardado en $SCHEMA:")
println("  analisis_vista, analisis_embudo, analisis_esfuerzo, analisis_segundo_embudo")