# events/models.py

from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError

class Organiser(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    def clean(self):
        if self.user.role != 'organiser':
            raise ValidationError("User must have organiser role to be an organiser")

    def __str__(self):
        return self.user.username

class Category(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class ParentEvent(models.Model):
    name = models.CharField(max_length=200)
    image = models.ImageField(upload_to="parent_events/", blank=True, null=True)  # OPTIONAL

    def __str__(self):
        return self.name


class Event(models.Model):
    parent_committee = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    parent_event = models.ForeignKey(
        ParentEvent, on_delete=models.SET_NULL, null=True, blank=True
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True
    )
    price = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    exclusivity = models.BooleanField(default=False)

    image = models.ImageField(upload_to="events/", blank=True, null=True)  # OPTIONAL

    organisers = models.ManyToManyField(
        Organiser, related_name='events', blank=True
    )

    invalid_qr_scans = models.PositiveIntegerField(default=0)
    rejected_scans = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name



BOOKING_TYPES = (
    ('single', 'Single'),
    ('multiple', 'Multiple'),
)

class ParticipationConstraint(models.Model):
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='constraint')

    booking_type = models.CharField(max_length=10, choices=BOOKING_TYPES)

    fixed = models.BooleanField(default=False)
    upper_limit = models.PositiveIntegerField(null=True, blank=True)
    lower_limit = models.PositiveIntegerField(null=True, blank=True)

    def clean(self):
        # Single booking → all limits must be null and fixed false
        if self.booking_type == 'single':
            if self.fixed or self.lower_limit or self.upper_limit:
                raise ValidationError("Single booking cannot have constraints")

        # Multiple booking + fixed true → only upper_limit required
        if self.booking_type == 'multiple' and self.fixed:
            if self.upper_limit is None:
                raise ValidationError("Upper limit required when fixed")
            if self.lower_limit is not None:
                raise ValidationError("Lower limit must be null when fixed")

        # Multiple booking + not fixed → lower and upper required
        if self.booking_type == 'multiple' and not self.fixed:
            if self.lower_limit is None or self.upper_limit is None:
                raise ValidationError("Lower & Upper limit both required")

    def __str__(self):
        return f"Constraint for {self.event.name}"


class EventDetails(models.Model):
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='details')

    description = models.TextField()
    venue = models.CharField(max_length=200)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()

    def __str__(self):
        return f"Details for {self.event.name}"



class EventSlot(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="slots"
    )

    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    unlimited_participants = models.BooleanField(default=True)
    max_participants = models.PositiveIntegerField(blank=True, null=True)

    booked_participants = models.PositiveIntegerField(default=0)
    available_participants = models.PositiveIntegerField(blank=True, null=True)
    available = models.BooleanField(default=True)

    class Meta:
        ordering = ["date", "start_time"]
        unique_together = ("event", "date", "start_time", "end_time")

    def clean(self):
        # rule 1: time logic
        if self.end_time <= self.start_time:
            raise ValidationError("End time must be after start time.")

        # rule 2: capacity logic
        if not self.unlimited_participants and (self.max_participants is None or self.max_participants <= 0):
            raise ValidationError("max_participants must be set when unlimited_participants is False.")

    def save(self, *args, **kwargs):
        self.clean()  # enforce validation even on save()

        if not self.unlimited_participants:
            self.available_participants = self.max_participants - self.booked_participants
            self.available = self.available_participants > 0
        else:
            self.available_participants = None
            self.available = True

        super().save(*args, **kwargs)

    def __str__(self):
        status = (
            "Unlimited"
            if self.unlimited_participants
            else f"{self.available_participants} available"
        )
        return f"{self.event.name} — {self.date} {self.start_time}-{self.end_time} ({status})"



class Cart(models.Model):
    """One active cart per user."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="carts")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Cart #{self.id} for {self.owner}"


class CartItem(models.Model):
    """
    A single event added to the cart. We store the intended participants_count here.
    For booking_type:
      - 'single'         -> participants_count = 1
      - 'multiple'+fixed -> participants_count = upper_limit
      - 'multiple'+open  -> participants_count chosen by user (between lower & upper)
    """
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="cart_items")
    participants_count = models.PositiveIntegerField(default=1)  # validated in clean()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("cart", "event")  # prevent duplicate same-event items in same cart
        ordering = ["-created_at"]

    def clean(self):
        # Validate against participation constraints
        constraint = getattr(self.event, "constraint", None)

        # If no constraint: treat as 'single'
        if constraint is None or constraint.booking_type == "single":
            if self.participants_count != 1:
                raise ValidationError("This event allows only a single participant.")
            return

        # multiple
        if constraint.fixed:
            if constraint.upper_limit is None:
                raise ValidationError("Event constraint is fixed but upper_limit is missing.")
            if self.participants_count != constraint.upper_limit:
                raise ValidationError(
                    f"This fixed event requires exactly {constraint.upper_limit} participants."
                )
            return

        # multiple and not fixed
        if constraint.lower_limit is None or constraint.upper_limit is None:
            raise ValidationError("Event constraint requires both lower and upper limits.")
        if not (constraint.lower_limit <= self.participants_count <= constraint.upper_limit):
            raise ValidationError(
                f"Participants must be between {constraint.lower_limit} and {constraint.upper_limit}."
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"CartItem #{self.id} — {self.event.name} x {self.participants_count}"


class TempBook(models.Model):
    """One row per participant detail collected for a cart item."""
    cart_item = models.ForeignKey(CartItem, on_delete=models.CASCADE, related_name="temp_participants")
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.name} ({self.email or ''}) for CartItem #{self.cart_item_id}"


class TempBookTimeslot(models.Model):
    """
    The chosen time slot for the given cart item.
    One-to-one with CartItem (one slot per cart item).
    """
    cart_item = models.OneToOneField(CartItem, on_delete=models.CASCADE, related_name="temp_timeslot")
    slot = models.ForeignKey("EventSlot", on_delete=models.CASCADE, related_name="temp_cart_selections")

    class Meta:
        unique_together = ("cart_item", "slot")

    def clean(self):
        # Slot must belong to the same event as cart_item
        if self.slot.event_id != self.cart_item.event_id:
            raise ValidationError("Selected slot does not belong to the same event as the cart item.")

        # Capacity check for non-unlimited slots
        if not self.slot.unlimited_participants:
            # Must have enough available_participants for this cart_item’s participant count
            needed = self.cart_item.participants_count
            if self.slot.available_participants is None:
                raise ValidationError("Slot capacity is not computed.")
            if self.slot.available_participants < needed:
                raise ValidationError("Selected slot does not have enough availability for your team size.")

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"CartItem #{self.cart_item_id} -> Slot #{self.slot_id}"





# events/models.py (append to the bottom)

from django.db import transaction

BOOKING_STATUS = (
    ("confirmed", "Confirmed"),
    ("cancelled", "Cancelled"),
)

PAYMENT_STATUS = (
    ("pending", "Pending"),
    ("paid", "Paid"),
    ("failed", "Failed"),
)

class Booking(models.Model):
    """Main booking header for a user checkout."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bookings"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Keep empty/optional now; you can fill after payment integration
    payment_status = models.CharField(
        max_length=20, choices=PAYMENT_STATUS,
        blank=True, null=True
    )

    status = models.CharField(max_length=20, choices=BOOKING_STATUS, default="confirmed")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"Booking #{self.id} by {self.user} ({self.status})"


class BookedEvent(models.Model):
    """One row per event in a booking (snapshot of cart item)."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="booked_events")
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="booked_events")
    slot = models.ForeignKey(EventSlot, on_delete=models.PROTECT, related_name="booked_events")

    # Snapshot data at the time of booking
    participants_count = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2, default=0)  # event.price at booking time
    line_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"BookedEvent #{self.id} — {self.event.name} (x{self.participants_count})"


import uuid

class BookedParticipant(models.Model):
    """All participants for a booked event; attendance tracking with QR."""

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="participants")
    booked_event = models.ForeignKey(BookedEvent, on_delete=models.CASCADE, related_name="participants")

    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    # Attendance
    arrived = models.BooleanField(default=False)
    checkin_time = models.DateTimeField(blank=True, null=True)
    scanned_by = models.ForeignKey('Organiser', null=True, blank=True, on_delete=models.SET_NULL, related_name='scanned_participants')
    reversed_by = models.ForeignKey('Organiser', null=True, blank=True, on_delete=models.SET_NULL, related_name='reversed_participants')
    reversed_time = models.DateTimeField(blank=True, null=True)

    # 🔥 STEP 1 (NO UNIQUE YET)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    qr_used = models.BooleanField(default=False)
    qr_generated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({'Arrived' if self.arrived else 'Not arrived'})"