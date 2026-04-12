import logging
import os

logger = logging.getLogger(__name__)


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

    # Normalizar número: solo dígitos, prefijo whatsapp:+
    digits = "".join(c for c in to_number if c.isdigit())
    if not digits:
        logger.warning("[TWILIO] Número de WhatsApp inválido: %s", to_number)
        return
    wa_to = f"whatsapp:+{digits}"

    body = (
        f"Nuevo turno en {shop_name}\n"
        f"Cliente: {client_name}\n"
        f"WhatsApp cliente: {whatsapp_cliente}\n"
        f"Barbero: {barber_name}\n"
        f"Servicio: {servicio}\n"
        f"Día: {fecha}\n"
        f"Hora: {hora}"
    )

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
