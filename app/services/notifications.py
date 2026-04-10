import logging
import os

logger = logging.getLogger(__name__)


def notify_barbershop(
    to_number: str,
    client_name: str,
    barber_name: str,
    shop_name: str,
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
        logger.warning("Twilio no configurado — omitiendo notificación")
        return

    # Normalizar número: solo dígitos, prefijo whatsapp:+
    digits = "".join(c for c in to_number if c.isdigit())
    if not digits:
        logger.warning("Número de WhatsApp inválido: %s", to_number)
        return
    wa_to = f"whatsapp:+{digits}"

    body = (
        f"Nuevo turno reservado en {shop_name}\n"
        f"Cliente: {client_name}\n"
        f"Barbero: {barber_name}\n"
        f"Día: {fecha}\n"
        f"Hora: {hora}"
    )

    try:
        from twilio.rest import Client
        Client(account_sid, auth_token).messages.create(
            body=body, from_=from_wa, to=wa_to
        )
        logger.info("Notificación enviada a %s", wa_to)
    except Exception as exc:
        logger.error("Error al enviar notificación Twilio: %s", exc)
