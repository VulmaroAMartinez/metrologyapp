"""Ventana fija del límite de tasa contra el stack real.

El límite cuenta en ventanas FIJAS de 60 s alineadas al reloj (`int(ahora // 60)`). Una prueba que
cuenta intentos (5 y luego 429, o el 429 en el intento 40) se rompe si el cambio de ventana cae a
mitad de su ejecución: el contador vuelve a cero. En el stack real no se puede congelar el reloj
del servidor, así que la prueba espera, solo si hace falta, a empezar al principio de una ventana
con tiempo de sobra. El servidor y las pruebas comparten el reloj del host (mismo kernel).
"""

import time

VENTANA = 60  # segundos; el mismo valor por defecto de apps.core.ratelimit.check_rate


def esperar_si_la_ventana_esta_por_terminar(necesario: float) -> None:
    """Garantiza que queden al menos `necesario` segundos en la ventana actual; si no, espera al
    comienzo de la siguiente (como mucho `necesario` segundos)."""
    restante = VENTANA - (time.time() % VENTANA)
    if restante < necesario:
        time.sleep(restante + 0.25)
