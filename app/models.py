import uuid
from datetime import datetime, timezone
from .extensions import db


def _uuid():
    return str(uuid.uuid4())


class User(db.Model):
    __tablename__ = "users"

    id         = db.Column(db.Integer, primary_key=True)
    dni        = db.Column(db.String(20), unique=True, nullable=False)
    name       = db.Column(db.String(100), nullable=False)
    whatsapp   = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id":       self.id,
            "dni":      self.dni,
            "name":     self.name,
            "whatsapp": self.whatsapp,
        }


class Shop(db.Model):
    __tablename__ = "shops"

    id                  = db.Column(db.String(36), primary_key=True, default=_uuid)
    slug                = db.Column(db.String(100), unique=True, nullable=False)
    name                = db.Column(db.String(200), nullable=False)
    logo_url            = db.Column(db.String(500))
    address             = db.Column(db.String(300))
    whatsapp            = db.Column(db.String(50))
    plan                = db.Column(db.String(20), default="solo")   # 'solo' | 'shop'
    flash_promo_active  = db.Column(db.Boolean, default=False)
    admin_password_hash = db.Column(db.String(256))
    created_at          = db.Column(db.DateTime(timezone=True),
                                    default=lambda: datetime.now(timezone.utc))

    barbers  = db.relationship("Barber",  back_populates="shop",
                                lazy="dynamic", foreign_keys="Barber.shop_id")
    services = db.relationship("Service", back_populates="shop", lazy="dynamic")

    def to_dict(self):
        return {
            "id":                 self.id,
            "slug":               self.slug,
            "name":               self.name,
            "logo_url":           self.logo_url,
            "address":            self.address,
            "whatsapp":           self.whatsapp,
            "plan":               self.plan,
            "flash_promo_active": self.flash_promo_active,
        }


class Barber(db.Model):
    __tablename__ = "barbers"

    id         = db.Column(db.String(36), primary_key=True, default=_uuid)
    # shop_id added via migration; nullable for backward-compat with pre-existing rows
    shop_id    = db.Column(db.String(36), db.ForeignKey("shops.id"), nullable=True)
    name       = db.Column(db.String(200), nullable=False)
    slug       = db.Column(db.String(100), unique=True, nullable=False)
    # Legacy denormalised columns – kept so existing raw-SQL queries still work
    shop_name  = db.Column(db.String(200))
    shop_slug  = db.Column(db.String(100))
    photo_url  = db.Column(db.String(500))
    instagram  = db.Column(db.String(200))
    specialty  = db.Column(db.String(200))
    bio        = db.Column(db.Text)
    whatsapp   = db.Column(db.String(50))
    is_active     = db.Column(db.Boolean, default=True)
    password_hash = db.Column(db.String(256), nullable=True)
    created_at    = db.Column(db.DateTime(timezone=True),
                              default=lambda: datetime.now(timezone.utc))

    shop         = db.relationship("Shop", back_populates="barbers", foreign_keys=[shop_id])
    appointments = db.relationship("Appointment", back_populates="barber", lazy="dynamic")

    def to_dict(self):
        return {
            "id":           self.id,
            "name":         self.name,
            "slug":         self.slug,
            "shop_id":      self.shop_id,
            "shop_name":    self.shop_name or (self.shop.name if self.shop else None),
            "shop_slug":    self.shop_slug or (self.shop.slug if self.shop else None),
            "photo_url":    self.photo_url,
            "instagram":    self.instagram,
            "specialty":    self.specialty,
            "bio":          self.bio,
            "whatsapp":     self.whatsapp,
            "is_active":    self.is_active,
            "has_password": bool(self.password_hash),
        }


class Service(db.Model):
    __tablename__ = "services"

    id               = db.Column(db.String(36), primary_key=True, default=_uuid)
    shop_id          = db.Column(db.String(36), db.ForeignKey("shops.id"), nullable=False)
    name             = db.Column(db.String(200), nullable=False)
    duration_minutes = db.Column(db.Integer, default=30)
    price            = db.Column(db.Numeric(10, 2), nullable=False)
    is_active        = db.Column(db.Boolean, default=True)
    display_order    = db.Column(db.Integer, default=0)
    created_at       = db.Column(db.DateTime(timezone=True),
                                 default=lambda: datetime.now(timezone.utc))

    shop = db.relationship("Shop", back_populates="services")

    def to_dict(self):
        return {
            "id":               self.id,
            "shop_id":          self.shop_id,
            "name":             self.name,
            "duration_minutes": self.duration_minutes,
            "price":            float(self.price),
            "is_active":        self.is_active,
            "display_order":    self.display_order,
        }


class Client(db.Model):
    __tablename__ = "clients"

    id         = db.Column(db.String(36), primary_key=True, default=_uuid)
    full_name  = db.Column(db.String(200), nullable=False)
    dni        = db.Column(db.String(50),  nullable=False)
    whatsapp   = db.Column(db.String(50))
    barber_id  = db.Column(db.String(36), db.ForeignKey("barbers.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc))


class Appointment(db.Model):
    __tablename__ = "appointments"

    id               = db.Column(db.String(36), primary_key=True, default=_uuid)
    barber_id        = db.Column(db.String(36), db.ForeignKey("barbers.id"), nullable=False)
    client_id        = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=True)
    # service_id added via migration; nullable for backward-compat
    service_id       = db.Column(db.String(36), db.ForeignKey("services.id"), nullable=True)
    appointment_time = db.Column(db.DateTime(timezone=True), nullable=False)
    status           = db.Column(db.String(20), default="available")
    service_name     = db.Column(db.String(200), default="Corte + Barba")
    price            = db.Column(db.Numeric(10, 2), default=3500)
    duration_minutes = db.Column(db.Integer, default=30)
    created_at       = db.Column(db.DateTime(timezone=True),
                                 default=lambda: datetime.now(timezone.utc))
    updated_at       = db.Column(db.DateTime(timezone=True),
                                 default=lambda: datetime.now(timezone.utc))
    cancelled_at          = db.Column(db.DateTime(timezone=True), nullable=True)
    # ── New columns (added via migration) ────────────────────────────────────
    whatsapp_number       = db.Column(db.String(50),  nullable=True)
    qr_token              = db.Column(db.String(255), nullable=True, unique=True)
    booking_code          = db.Column(db.String(20),  nullable=True, unique=True)
    rescheduled_count     = db.Column(db.Integer,     default=0)
    absence_charge_sent   = db.Column(db.Boolean,     default=False)
    absence_charge_amount = db.Column(db.Integer,     default=0)
    terms_accepted_at     = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("barber_id", "appointment_time", name="uq_barber_time"),
    )

    barber  = db.relationship("Barber",  back_populates="appointments")
    service = db.relationship("Service")
