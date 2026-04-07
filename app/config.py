import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-key")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "").replace(
        "postgres://", "postgresql://"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CORS_ORIGINS = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,https://barbershop-saas-1yart.vercel.app"
    ).split(",")

    # ── Booking rules ──────────────────────────────────────────────────────────
    CANCEL_WINDOW_MINUTES  = int(os.environ.get("CANCEL_WINDOW_MINUTES",  90))
    LATE_TOLERANCE_MINUTES = int(os.environ.get("LATE_TOLERANCE_MINUTES",  8))
    MAX_RESCHEDULES        = int(os.environ.get("MAX_RESCHEDULES",          1))
    ABSENCE_CHARGE_PERCENT = int(os.environ.get("ABSENCE_CHARGE_PERCENT", 30))
    MERCADOPAGO_ALIAS      = os.environ.get("MERCADOPAGO_ALIAS", "resquin.mvz")
    FRONTEND_URL           = os.environ.get(
        "FRONTEND_URL", "https://barbershop-saas-1yart.vercel.app"
    )
