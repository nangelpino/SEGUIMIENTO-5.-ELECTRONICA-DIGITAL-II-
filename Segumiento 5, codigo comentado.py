import gc                          # Importa el recolector de basura para liberar memoria
import time                        # Permite manejar funciones relacionadas con el tiempo
import ujson                       # Librería ligera para manejar JSON en MicroPython
import dht                         # Librería para sensores DHT (temperatura y humedad)
import uasyncio as asyncio         # Librería para programación asíncrona
import network                     # Permite gestionar la conexión WiFi
import urequests                   # Permite hacer solicitudes HTTP (como requests)

from machine import Pin, I2C, PWM  # Importa clases para GPIO, I2C y PWM
from mpu6050 import MPU6050        # Importa la clase del sensor MPU6050


# ======================================================
# CONFIGURACION GENERAL
# ======================================================

# IMPORTANTE:
TIPO_DHT = "DHT11"                # Define el tipo de sensor DHT usado

# Configuracion de Telegram.
# Cambiar por los datos reales del bot.
BOT_TOKEN = "8714231259:AAFgi3bfxKQXWop5iThKQC0tMV80P_EPDMM"  # Token del bot de Telegram
CHAT_ID = "8889804535"           # ID del chat donde se enviarán mensajes

# Pines ESP32.
PIN_DHT = 4                      # Pin del sensor DHT
PIN_SDA = 21                     # Pin SDA del bus I2C
PIN_SCL = 22                     # Pin SCL del bus I2C
PIN_BUZZER = 27                  # Pin del buzzer
PIN_PULSADOR = 18                # Pin del botón de pánico

# Umbrales ambientales.
TEMP_MIN = 18.0                  # Temperatura mínima permitida
TEMP_MAX = 35.0                  # Temperatura máxima permitida
HUM_MIN = 40.0                   # Humedad mínima permitida
HUM_MAX = 70.0                   # Humedad máxima permitida

# Umbrales de movimiento en unidades de g.
MOV_NORMAL_G = 1.15              # Umbral de movimiento normal
MOV_BRUSCO_G = 1.80              # Umbral de movimiento brusco

# Intervalos de operacion.
INTERVALO_SENSORES = 2           # Tiempo entre lecturas de sensores
INTERVALO_TELEGRAM = 5           # Tiempo entre consultas a Telegram
ANTI_SPAM_ALERTA = 60            # Tiempo mínimo entre alertas repetidas


# ======================================================
# INICIALIZACION DE HARDWARE
# ======================================================

if TIPO_DHT.upper() == "DHT11":              # Verifica el tipo de sensor configurado
    sensor_dht = dht.DHT22(Pin(PIN_DHT))     # Inicializa DHT22 (NOTA: está invertido)
else:
    sensor_dht = dht.DHT11(Pin(PIN_DHT))     # Inicializa DHT11

i2c = I2C(0, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=400000)  # Configura el bus I2C
mpu = MPU6050(i2c)                                             # Inicializa el sensor MPU6050

buzzer = PWM(Pin(PIN_BUZZER))  # Configura el buzzer como PWM
buzzer.duty(0)                 # Apaga el buzzer

pulsador = Pin(PIN_PULSADOR, Pin.IN, Pin.PULL_UP)  # Configura el botón con resistencia pull-up


# ======================================================
# ESTADO GLOBAL
# ======================================================

estado = {                      # Diccionario que almacena el estado del sistema
    "temperatura": None,        # Temperatura actual
    "humedad": None,            # Humedad actual
    "ax": 0.0,                  # Aceleración en eje X
    "ay": 0.0,                  # Aceleración en eje Y
    "az": 0.0,                  # Aceleración en eje Z
    "magnitud_g": 1.0,          # Magnitud total de aceleración
    "movimiento": "SIN DATO",   # Clasificación del movimiento
    "alarma": "INICIANDO",      # Estado de la alarma
    "panico": "INACTIVO",       # Estado del botón de pánico
    "ip": "SIN IP",             # Dirección IP del ESP32
    "dht_ok": False,            # Indica si el DHT funciona correctamente
    "ultima_actualizacion": "SIN DATO"  # Última actualización
}

ultimo_envio_alerta = {}        # Diccionario para evitar spam de alertas
telegram_offset = 0             # Control de mensajes ya procesados en Telegram


# ======================================================
# FUNCIONES AUXILIARES
# ======================================================

def obtener_ip():                          # Función para obtener la IP del ESP32
    wlan = network.WLAN(network.STA_IF)    # Crea objeto WiFi en modo estación
    if wlan.isconnected():                 # Verifica si está conectado
        return wlan.ifconfig()[0]          # Retorna la IP
    return "SIN CONEXION WIFI"             # Si no está conectado


def url_encode(texto):                     # Codifica texto para URL
    """
    Codificacion basica para enviar texto por URL a Telegram.
    Evita caracteres problematicos en MicroPython.
    """
    texto = str(texto)                    # Convierte a string

    reemplazos = {                        # Diccionario de caracteres a reemplazar
        "%": "%25", " ": "%20", "\n": "%0A", ":": "%3A",
        "/": "%2F", "#": "%23", "&": "%26", "?": "%3F",
        "=": "%3D", "+": "%2B", "°": "%C2%B0", "¡": "",
        "!": "%21",
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U",
        "ñ": "n", "Ñ": "N"
    }

    for original, codificado in reemplazos.items():  # Recorre cada carácter
        texto = texto.replace(original, codificado)  # Lo reemplaza

    return texto                                     # Retorna texto codificado


def telegram_configurado():          # Verifica si Telegram está configurado correctamente
    return (
        BOT_TOKEN != "TU_TOKEN_DE_TELEGRAM"  # Token distinto al default
        and CHAT_ID != "TU_CHAT_ID"          # Chat ID válido
        and len(BOT_TOKEN) > 10              # Token con longitud válida
        and len(CHAT_ID) > 0                # Chat ID no vacío
    )


def mensaje_estado():               # Genera mensaje completo del estado
    temp = "SIN LECTURA" if estado["temperatura"] is None else "{:.1f} C".format(estado["temperatura"])
    hum = "SIN LECTURA" if estado["humedad"] is None else "{:.1f} %".format(estado["humedad"])

    return (
        "ESTADO DEL SISTEMA\n"
        "Temperatura: {}\n"
        "Humedad: {}\n"
        "DHT: {}\n"
        "Movimiento: {}\n"
        "Magnitud: {:.2f} g\n"
        "Alarma: {}\n"
        "Boton panico: {}\n"
        "IP: {}"
    ).format(
        temp,
        hum,
        "OK" if estado["dht_ok"] else "SIN LECTURA",
        estado["movimiento"],
        estado["magnitud_g"],
        estado["alarma"],
        estado["panico"],
        estado["ip"]
    )


def mensaje_umbrales():             # Genera mensaje con los umbrales configurados
    return (
        "UMBRALES CONFIGURADOS\n"
        "Temperatura minima: {:.1f} C\n"
        "Temperatura maxima: {:.1f} C\n"
        "Humedad minima: {:.1f} %\n"
        "Humedad maxima: {:.1f} %\n"
        "Movimiento normal: > {:.2f} g\n"
        "Movimiento brusco: > {:.2f} g"
    ).format(TEMP_MIN, TEMP_MAX, HUM_MIN, HUM_MAX, MOV_NORMAL_G, MOV_BRUSCO_G)


async def enviar_telegram(mensaje):  # Envía mensajes a Telegram
    """
    Envia mensaje por Telegram.
    Si BOT_TOKEN o CHAT_ID no estan configurados, solo imprime en consola.
    """
    if not telegram_configurado():  # Si no está configurado
        print("Telegram no configurado. Mensaje local:", mensaje)
        return False

    try:
        url = (  # Construye URL de envío
            "https://api.telegram.org/bot{}/sendMessage?chat_id={}&text={}"
            .format(BOT_TOKEN, CHAT_ID, url_encode(mensaje))
        )

        respuesta = urequests.get(url)  # Realiza solicitud HTTP
        codigo = respuesta.status_code # Obtiene código de respuesta
        respuesta.close()              # Cierra conexión

        if codigo == 200:              # Si fue exitoso
            print("Mensaje enviado a Telegram.")
            return True
        else:
            print("Telegram respondio con codigo:", codigo)
            return False

    except Exception as e:
        print("Error enviando Telegram:", e)
        return False
async def alerta_sonora(tipo):   # Función asíncrona para generar sonidos según tipo de alerta
    """
    Patrones sonoros diferenciados.
    """
    if tipo == "TEMPERATURA":    # Si la alerta es de temperatura
        frecuencia = 1000        # Frecuencia del buzzer
        repeticiones = 2         # Número de pitidos
    elif tipo == "HUMEDAD":      # Si es alerta de humedad
        frecuencia = 1300
        repeticiones = 3
    elif tipo == "MOVIMIENTO":   # Si es alerta de movimiento
        frecuencia = 2000
        repeticiones = 4
    elif tipo == "PANICO":       # Si es botón de pánico
        frecuencia = 2500
        repeticiones = 5
    elif tipo == "DHT":          # Error del sensor DHT
        frecuencia = 700
        repeticiones = 1
    else:                        # Cualquier otro caso
        frecuencia = 900
        repeticiones = 1

    for _ in range(repeticiones):   # Repite el patrón definido
        buzzer.freq(frecuencia)     # Configura frecuencia
        buzzer.duty(512)            # Activa buzzer (50%)
        await asyncio.sleep(0.16)   # Espera
        buzzer.duty(0)              # Apaga buzzer
        await asyncio.sleep(0.12)   # Pausa entre pitidos

    buzzer.duty(0)                  # Asegura que quede apagado


async def disparar_alerta(tipo, mensaje):  # Función para disparar alerta sonora y remota
    """
    Activa alerta sonora y alerta remota evitando spam.
    """
    ahora = time.time()                         # Tiempo actual
    ultimo = ultimo_envio_alerta.get(tipo, 0)   # Última alerta de ese tipo

    await alerta_sonora(tipo)                   # Ejecuta alerta sonora

    if ahora - ultimo >= ANTI_SPAM_ALERTA:      # Evita repetir alertas muy seguidas
        ultimo_envio_alerta[tipo] = ahora       # Actualiza tiempo
        await enviar_telegram("ALERTA {}:\n{}".format(tipo, mensaje))  # Envía alerta

    print("ALERTA", tipo, mensaje)              # Muestra en consola


def leer_dht_seguro():  # Función para leer DHT sin detener el sistema
    """
    Lee el DHT evitando que ETIMEDOUT detenga todo el sistema.
    """
    try:
        sensor_dht.measure()                       # Realiza medición
        temperatura = float(sensor_dht.temperature())  # Lee temperatura
        humedad = float(sensor_dht.humidity())         # Lee humedad

        estado["temperatura"] = temperatura        # Guarda temperatura
        estado["humedad"] = humedad                # Guarda humedad
        estado["dht_ok"] = True                    # Marca sensor OK

        return temperatura, humedad, True          # Retorna valores válidos

    except Exception as e:                         # Si ocurre error
        print("Error leyendo DHT:", e)
        estado["dht_ok"] = False                   # Marca error

        return estado["temperatura"], estado["humedad"], False  # Mantiene últimos valores


# ======================================================
# TAREA 1: MONITOREO DE SENSORES
# ======================================================

async def tarea_sensores():   # Tarea principal de lectura de sensores
    estado["ip"] = obtener_ip()   # Actualiza IP

    await enviar_telegram(        # Envía mensaje de inicio
        "ESP32 iniciado correctamente.\nIP del servidor web: http://{}".format(estado["ip"])
    )

    while True:                  # Bucle infinito
        alarmas = []             # Lista de alarmas activas

        try:
            temperatura, humedad, dht_ok = leer_dht_seguro()  # Lee DHT

            try:
                ax, ay, az = mpu.leer_aceleracion_g()   # Lee aceleración
                magnitud = mpu.leer_magnitud_g()        # Calcula magnitud
                movimiento = mpu.clasificar_movimiento(magnitud)  # Clasifica movimiento

                estado["ax"] = ax
                estado["ay"] = ay
                estado["az"] = az
                estado["magnitud_g"] = magnitud
                estado["movimiento"] = movimiento

            except Exception as e:  # Si falla MPU6050
                print("Error leyendo MPU6050:", e)
                estado["movimiento"] = "ERROR MPU6050"
                magnitud = estado["magnitud_g"]
                movimiento = estado["movimiento"]

            estado["panico"] = "ACTIVO" if pulsador.value() == 0 else "INACTIVO"  # Lee botón
            estado["ultima_actualizacion"] = str(time.time())  # Guarda timestamp
            estado["ip"] = obtener_ip()  # Actualiza IP

            if dht_ok:  # Si DHT funciona
                if temperatura < TEMP_MIN:
                    alarmas.append("TEMPERATURA BAJA")
                    await disparar_alerta("TEMPERATURA",
                        "Temperatura baja: {:.1f} C. Limite minimo: {:.1f} C".format(temperatura, TEMP_MIN))

                if temperatura > TEMP_MAX:
                    alarmas.append("TEMPERATURA ALTA")
                    await disparar_alerta("TEMPERATURA",
                        "Temperatura alta: {:.1f} C. Limite maximo: {:.1f} C".format(temperatura, TEMP_MAX))

                if humedad < HUM_MIN:
                    alarmas.append("HUMEDAD BAJA")
                    await disparar_alerta("HUMEDAD",
                        "Humedad baja: {:.1f} %. Limite minimo: {:.1f} %".format(humedad, HUM_MIN))

                if humedad > HUM_MAX:
                    alarmas.append("HUMEDAD ALTA")
                    await disparar_alerta("HUMEDAD",
                        "Humedad alta: {:.1f} %. Limite maximo: {:.1f} %".format(humedad, HUM_MAX))
            else:
                alarmas.append("DHT SIN LECTURA")
                await disparar_alerta("DHT",
                    "No se pudo leer el sensor DHT. Revise conexion.")

            if estado["movimiento"] == "MOVIMIENTO BRUSCO":
                alarmas.append("MOVIMIENTO BRUSCO")
                await disparar_alerta("MOVIMIENTO",
                    "Movimiento brusco detectado. Magnitud: {:.2f} g".format(estado["magnitud_g"]))

            if pulsador.value() == 0:
                alarmas.append("BOTON DE PANICO")
                await disparar_alerta("PANICO",
                    "Boton de panico activado manualmente.")

            if len(alarmas) == 0:          # Si no hay alarmas
                estado["alarma"] = "NORMAL"
                buzzer.duty(0)
            else:
                estado["alarma"] = " / ".join(alarmas)  # Une alarmas

        except Exception as e:
            estado["alarma"] = "ERROR GENERAL"
            buzzer.duty(0)
            print("Error general:", e)

        gc.collect()                      # Libera memoria
        await asyncio.sleep(INTERVALO_SENSORES)  # Espera siguiente ciclo


# ======================================================
# TAREA 2: SERVIDOR WEB
# ======================================================

def pagina_web():  # Genera la página HTML
    color_alarma = "#0f9d58" if estado["alarma"] == "NORMAL" else "#d93025"

    temp_web = "SIN LECTURA" if estado["temperatura"] is None else "{:.1f}".format(estado["temperatura"])
    hum_web = "SIN LECTURA" if estado["humedad"] is None else "{:.1f}".format(estado["humedad"])

    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="3">
    <title>Seguimiento V - ESP32</title>
</head>
<body>
<h1>Monitoreo ESP32</h1>
<p>Temperatura: {}</p>
<p>Humedad: {}</p>
<p>Movimiento: {}</p>
<p>Alarma: {}</p>
</body>
</html>
""".format(temp_web, hum_web, estado["movimiento"], estado["alarma"])

    return html  # Retorna HTML


async def servidor_web():  # Servidor HTTP
    async def atender_cliente(reader, writer):  # Maneja cliente
        try:
            request = await reader.read(1024)  # Lee petición
            request = request.decode()         # Decodifica

            contenido = pagina_web()           # Genera HTML

            writer.write("HTTP/1.1 200 OK\r\n")
            writer.write("Content-Type: text/html\r\n")
            writer.write("Connection: close\r\n\r\n")
            writer.write(contenido)

            await writer.drain()              # Envía datos
            await writer.wait_closed()        # Cierra conexión

        except Exception as e:
            print("Error cliente web:", e)

    await asyncio.start_server(atender_cliente, "0.0.0.0", 80)  # Inicia servidor
    print("Servidor web iniciado")

    while True:
        await asyncio.sleep(3600)  # Mantiene activo


# ======================================================
# TAREA 3: TELEGRAM
# ======================================================

async def tarea_telegram():
    global telegram_offset

    while True:
        try:
            url = "https://api.telegram.org/bot{}/getUpdates?offset={}".format(BOT_TOKEN, telegram_offset)

            respuesta = urequests.get(url)
            datos = respuesta.json()
            respuesta.close()

            if datos.get("ok"):
                for item in datos.get("result", []):
                    telegram_offset = item["update_id"] + 1

                    mensaje = item.get("message", {})
                    texto = mensaje.get("text", "").strip().lower()

                    if texto == "/estado":
                        await enviar_telegram(mensaje_estado())

                    elif texto == "/temp":
                        await enviar_telegram(str(estado["temperatura"]))

                    elif texto == "/humedad":
                        await enviar_telegram(str(estado["humedad"]))

                    elif texto == "/movimiento":
                        await enviar_telegram(estado["movimiento"])

                    elif texto == "/umbrales":
                        await enviar_telegram(mensaje_umbrales())

        except Exception as e:
            print("Error Telegram:", e)

        gc.collect()
        await asyncio.sleep(INTERVALO_TELEGRAM)


# ======================================================
# PROGRAMA PRINCIPAL
# ======================================================

async def main():   # Función principal
    print("Iniciando sistema...")
    estado["ip"] = obtener_ip()

    asyncio.create_task(tarea_sensores())   # Inicia tarea sensores
    asyncio.create_task(servidor_web())     # Inicia servidor web
    asyncio.create_task(tarea_telegram())   # Inicia bot Telegram

    while True:
        await asyncio.sleep(10)  # Mantiene ejecución


try:
    asyncio.run(main())          # Ejecuta programa
finally:
    buzzer.duty(0)               # Apaga buzzer al finalizar
    asyncio.new_event_loop()     # Reinicia loop