from flask import Flask
from flask_cors import CORS
from .config import Config
from .extensions import db

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    CORS(app, origins="*")

    from .api.appointments import bp as appts_bp
    from .api.dashboard    import bp as dashboard_bp
    from .api.barbers      import bp as barbers_bp
    from .api.shops        import bp as shops_bp
    from .api.admin        import bp as admin_bp
    from .api.users        import bp as users_bp
    from .api.barbershop   import bp as barbershop_bp

    app.register_blueprint(appts_bp,      url_prefix="/api/v1/appointments")
    app.register_blueprint(dashboard_bp,  url_prefix="/api/v1/barber")
    app.register_blueprint(barbers_bp,    url_prefix="/api/v1/barbers")
    app.register_blueprint(shops_bp,      url_prefix="/api/v1/shops")
    app.register_blueprint(admin_bp,      url_prefix="/api/v1/admin")
    app.register_blueprint(users_bp,      url_prefix="/api/v1/users")
    app.register_blueprint(barbershop_bp, url_prefix="/api/v1/barbershop")

    # Create new tables (shops, services) and add missing columns to existing ones
    with app.app_context():
        from . import models  # noqa: register ORM classes
        db.create_all()
        _run_migrations()

    @app.get("/health")
    def health():
        return {"status": "ok", "message": "Backend funcionando!"}

    @app.post("/internal/generate-daily-slots")
    def generate_daily_slots():
        from sqlalchemy import text
        from datetime import datetime, timezone, timedelta
        import pytz

        ART       = pytz.timezone("America/Argentina/Buenos_Aires")
        BARBER_ID = "e6f0681a-9724-4425-9f5e-1ee188899e02"
        today     = datetime.now(ART).date()
        total     = 0

        for day_offset in range(3):
            target_date = today + timedelta(days=day_offset)
            current_hour, current_min = 9, 0

            while True:
                local_dt = ART.localize(datetime(
                    target_date.year, target_date.month, target_date.day,
                    current_hour, current_min
                ))
                utc_dt = local_dt.astimezone(timezone.utc)

                db.session.execute(text("""
                    INSERT INTO appointments 
                        (barber_id, appointment_time, status, service_name, price)
                    VALUES 
                        (:bid, :appt_time, 'available', 'Corte + Barba', 3500)
                    ON CONFLICT (barber_id, appointment_time) DO NOTHING
                """), {"bid": BARBER_ID, "appt_time": utc_dt})

                total += 1
                current_min += 30
                if current_min >= 60:
                    current_min -= 60
                    current_hour += 1
                if current_hour >= 18:
                    break

        db.session.commit()
        return {"status": "ok", "slots_generados": total}

    return app


def _run_migrations():
    """Idempotent: add new columns to pre-existing tables."""
    from sqlalchemy import text
    stmts = [
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS shop_id    VARCHAR(36)",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS shop_name  VARCHAR(200)",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS shop_slug  VARCHAR(100)",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS photo_url  VARCHAR(500)",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS instagram  VARCHAR(200)",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS specialty  VARCHAR(200)",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS bio        TEXT",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS whatsapp   VARCHAR(50)",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS is_active  BOOLEAN DEFAULT TRUE",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS service_id            VARCHAR(36)",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS cancelled_at          TIMESTAMPTZ",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS created_at            TIMESTAMPTZ DEFAULT NOW()",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS updated_at            TIMESTAMPTZ DEFAULT NOW()",
        # ── New columns ────────────────────────────────────────────────────────
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS whatsapp_number       VARCHAR(50)",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS qr_token              VARCHAR(255)",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS booking_code          VARCHAR(20)",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS rescheduled_count     INTEGER DEFAULT 0",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS absence_charge_sent   BOOLEAN DEFAULT FALSE",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS absence_charge_amount INTEGER DEFAULT 0",
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS terms_accepted_at     TIMESTAMPTZ",
        "ALTER TABLE barbers      ADD COLUMN IF NOT EXISTS password_hash         VARCHAR(256)",
        # ── users table ────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS users (
            id         SERIAL PRIMARY KEY,
            dni        VARCHAR(20) UNIQUE NOT NULL,
            name       VARCHAR(100) NOT NULL,
            whatsapp   VARCHAR(20) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)",
        # Unique indexes (IF NOT EXISTS para idempotencia)
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_appt_qr_token      ON appointments (qr_token)      WHERE qr_token IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_appt_booking_code  ON appointments (booking_code)  WHERE booking_code IS NOT NULL",
        # ── blocked_slots table ────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS blocked_slots (
            id           SERIAL PRIMARY KEY,
            barber_id    UUID REFERENCES barbers(id) ON DELETE CASCADE,
            blocked_date DATE        NOT NULL,
            blocked_time TIME,
            all_day      BOOLEAN     NOT NULL DEFAULT FALSE,
            reason       VARCHAR(100),
            created_at   TIMESTAMP   DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_blocked_barber_date ON blocked_slots(barber_id, blocked_date)",
        # ── shops.owner_email (auto-registro multi-tenant) ─────────────────────
        "ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_email VARCHAR(200)",
    ]
    for sql in stmts:
        try:
            db.session.execute(text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()