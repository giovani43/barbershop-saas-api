import logging
import os

logger = logging.getLogger(__name__)


def formatear_telefono(tel: str) -> str:
    tel = tel.strip().replace(" ", "").replace("-", "")
    if not tel.startswith("+"):
        # bare number like 1133223802 → +5491133223802
        tel = "+549" + tel
    elif tel.startswith("+54") and not tel.startswith("+549"):
        # +541133223802 → +5491133223802 (insert 9 after country code)
        tel = "+549" + tel[3:]
    return "whatsapp:" + tel


def _twilio_send(to_number: str, body: str, label: str) -> None:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token  = os.environ.get("TWILIO_AUTH_TOKEN")
    from_wa     = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    if not account_sid or not auth_token:
        logger.warning("[TWILIO %s] No configurado — omitiendo", label)
        return
    if not to_number or not to_number.strip():
        logger.info("[TWILIO %s] Número vacío — omitiendo", label)
        return
    wa_to = formatear_telefono(to_number)
    logger.info("[TWILIO %s] Enviando a %s", label, wa_to)
    print(f"[TWILIO {label}] Enviando a {wa_to}")
    try:
        from twilio.rest import Client
        msg = Client(account_sid, auth_token).messages.create(
            body=body, from_=from_wa, to=wa_to
        )
        logger.info("[TWILIO %s] OK, SID=%s", label, msg.sid)
        print(f"[TWILIO {label}] OK, SID={msg.sid}")
    except Exception as exc:
        logger.error("[TWILIO ERROR %s] %s", label, exc)
        print(f"[TWILIO ERROR {label}] {exc}")


def notify_cancel_barbershop(
    to_number: str,
    client_name: str,
    whatsapp_cliente: str,
    barber_name: str,
    shop_name: str,
    servicio: str,
    fecha: str,
    hora: str,
) -> None:
    body = (
        f"❌ Turno cancelado\n"
        f"Cliente: {client_name}\n"
        f"WhatsApp: {whatsapp_cliente}\n"
        f"Servicio: {servicio}\n"
        f"Día: {fecha}\n"
        f"Hora: {hora}"
    )
    _twilio_send(to_number, body, "cancel-barbershop")


def notify_cancel_cliente(
    to_number: str,
    barber_name: str,
    shop_name: str,
    servicio: str,
    fecha: str,
    hora: str,
) -> None:
    body = (
        f"❌ Tu turno en {shop_name} fue cancelado.\n"
        f"Barbero: {barber_name}\n"
        f"Servicio: {servicio}\n"
        f"Día: {fecha}\n"
        f"Hora: {hora}"
    )
    _twilio_send(to_number, body, "cancel-cliente")


def notify_reschedule_barbershop(
    to_number: str,
    client_name: str,
    whatsapp_cliente: str,
    barber_name: str,
    shop_name: str,
    servicio: str,
    fecha: str,
    hora: str,
) -> None:
    body = (
        f"🔄 Turno reprogramado\n"
        f"Cliente: {client_name}\n"
        f"WhatsApp: {whatsapp_cliente}\n"
        f"Servicio: {servicio}\n"
        f"Nuevo día: {fecha}\n"
        f"Nueva hora: {hora}"
    )
    _twilio_send(to_number, body, "reschedule-barbershop")


def notify_reschedule_cliente(
    to_number: str,
    barber_name: str,
    shop_name: str,
    servicio: str,
    fecha: str,
    hora: str,
    booking_code: str,
) -> None:
    body = (
        f"🔄 Tu turno en {shop_name} fue reprogramado.\n"
        f"Barbero: {barber_name}\n"
        f"Servicio: {servicio}\n"
        f"Nuevo día: {fecha}\n"
        f"Nueva hora: {hora}\n"
        f"Código: {booking_code}"
    )
    _twilio_send(to_number, body, "reschedule-cliente")


def notify_cliente(
    to_number: str,
    barber_name: str,
    shop_name: str,
    servicio: str,
    fecha: str,
    hora: str,
    booking_code: str,
) -> None:
    if not to_number or not to_number.strip():
        logger.info("[TWILIO] Cliente sin número — omitiendo notificación")
        return

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token  = os.environ.get("TWILIO_AUTH_TOKEN")
    from_wa     = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    if not account_sid or not auth_token:
        logger.warning("[TWILIO] No configurado — omitiendo notificación cliente")
        return

    wa_to = formatear_telefono(to_number)
    body = (
        f"✅ ¡Turno confirmado en {shop_name}!\n"
        f"Barbero: {barber_name}\n"
        f"Servicio: {servicio}\n"
        f"Día: {fecha}\n"
        f"Hora: {hora}\n"
        f"Código: {booking_code}\n\n"
        f"Guardá este código para cancelar o reprogramar."
    )

    logger.info("[TWILIO] Enviando confirmación a cliente %s", wa_to)
    print(f"[TWILIO] Enviando confirmación cliente a {wa_to}")
    try:
        from twilio.rest import Client
        message = Client(account_sid, auth_token).messages.create(
            body=body, from_=from_wa, to=wa_to
        )
        print(f"[TWILIO] Confirmación cliente OK, SID={message.sid}")
        logger.info("[TWILIO] Confirmación cliente enviada a %s, SID=%s", wa_to, message.sid)
    except Exception as exc:
        print(f"[TWILIO ERROR cliente] {str(exc)}")
        logger.error("[TWILIO ERROR cliente] %s", exc)


def notify_barbershop(
    to_number: str,
    client_name: str,
    whatsapp_cliente: str,
    barber_name: str,
    shop_name: str,
    servicio: str,
    fecha: str,
    hora: str,
) -> None:
    """
    Envía un WhatsApp al número de la barbería via Twilio Sandbox.
    No bloquea la respuesta al cliente — los errores solo se loguean.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token  = os.environ.get("TWILIO_AUTH_TOKEN")
    from_wa     = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    if not account_sid or not auth_token:
        logger.warning("[TWILIO] No configurado — omitiendo notificación")
        print("[TWILIO] ADVERTENCIA: TWILIO_ACCOUNT_SID o TWILIO_AUTH_TOKEN no están seteados")
        return

    if not to_number or not to_number.strip():
        logger.warning("[TWILIO] Número de WhatsApp inválido: %s", to_number)
        return
    wa_to = formatear_telefono(to_number)

    body = (
        f"Nuevo turno en {shop_name}\n"
        f"Cliente: {client_name}\n"
        f"WhatsApp cliente: {whatsapp_cliente}\n"
        f"Barbero: {barber_name}\n"
        f"Servicio: {servicio}\n"
        f"Día: {fecha}\n"
        f"Hora: {hora}"
    )

    logger.info("[TWILIO] Número destino formateado: %s", wa_to)
    print(f"[TWILIO] Enviando a {wa_to}")
    try:
        from twilio.rest import Client
        message = Client(account_sid, auth_token).messages.create(
            body=body, from_=from_wa, to=wa_to
        )
        print(f"[TWILIO] Enviado OK, SID={message.sid}")
        logger.info("[TWILIO] Notificación enviada a %s, SID=%s", wa_to, message.sid)
    except Exception as exc:
        print(f"[TWILIO ERROR] {str(exc)}")
        logger.error("[TWILIO ERROR] %s", exc)
