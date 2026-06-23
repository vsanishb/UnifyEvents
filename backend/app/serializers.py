# events/serializers.py

from rest_framework import serializers
from .models import *
from django.contrib.auth import get_user_model

User = get_user_model()


class OrganiserSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()

    class Meta:
        model = Organiser
        fields = ['id', 'user', 'user_display']   
    def get_user_display(self, obj):
        return f"{obj.user.username} ({obj.user.email})"

    def validate_user(self, value):
        if value.role != 'organiser':
            raise serializers.ValidationError("User is not organiser role")
        return value


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']

class ParentEventSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = ParentEvent
        fields = ['id', 'name', 'image']


class EventSerializer(serializers.ModelSerializer):
    constraint_id = serializers.IntegerField(source="constraint.id", read_only=True)
    details_id = serializers.IntegerField(source="details.id", read_only=True)

    organisers = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Organiser.objects.all(), required=False
    )


    parent_committee = serializers.CharField(required=False)
    name = serializers.CharField(required=False)
    slots_count = serializers.SerializerMethodField()


    parent_event = serializers.PrimaryKeyRelatedField(
        queryset=ParentEvent.objects.all(),
        allow_null=True,
        required=False
    )

    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        allow_null=True,
        required=False
    )

    price = serializers.DecimalField(max_digits=9, decimal_places=2, required=False)
    exclusivity = serializers.BooleanField(required=False)
    image_key = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            'id',
            'parent_committee',
            'name',
            'parent_event',
            'category',
            'price',
            'exclusivity',
            'organisers',
            'image',       # ✅ add this
            "image_key",
            'constraint_id',
            'details_id',
            'slots_count'
        ]
        extra_kwargs = {
            "image": {"write_only": True, "required": False}
        }

    
    def get_slots_count(self, obj):
        return obj.slots.count()


    def get_image_key(self, obj):
        return obj.image.name if obj.image else None
    
    def get_constraint_id(self, obj):
        return obj.constraint.id if hasattr(obj, "constraint") else None

    def get_details_id(self, obj):
        return obj.details.id if hasattr(obj, "details") else None



class EventSlotSerializer(serializers.ModelSerializer):
    event_name = serializers.CharField(source="event.name", read_only=True)

    class Meta:
        model = EventSlot
        fields = (
            "id",
            "event",
            "event_name",
            "date",
            "start_time",
            "end_time",
            "max_participants",
            "unlimited_participants",
            "available_participants",
            "booked_participants",
            "available",
        )

    def validate(self, data):
        unlimited = data.get(
            "unlimited_participants",
            getattr(self.instance, "unlimited_participants", True)
        )
        max_participants = data.get(
            "max_participants",
            getattr(self.instance, "max_participants", None)
        )

        start_time = data.get("start_time", getattr(self.instance, "start_time", None))
        end_time = data.get("end_time", getattr(self.instance, "end_time", None))

        # validation for capacity
        if not unlimited and not max_participants:
            raise serializers.ValidationError({
                "max_participants": "This field is required when unlimited_participants is False."
            })

        # validation for time
        if start_time and end_time and end_time <= start_time:
            raise serializers.ValidationError({
                "end_time": "End time must be after start time."
            })

        return data

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if instance.unlimited_participants:
            rep["available_participants"] = None
        return rep



class ParticipationConstraintSerializer(serializers.ModelSerializer):
    event = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all())
    lower_limit = serializers.IntegerField(required=False, allow_null=True)
    upper_limit = serializers.IntegerField(required=False, allow_null=True)
    fixed = serializers.BooleanField(required=False)

    class Meta:
        model = ParticipationConstraint
        fields = [
            'id', 'event',
            'booking_type',
            'fixed', 'upper_limit', 'lower_limit'
        ]

    def validate(self, data):
        """
        Ensure logical consistency between booking_type, fixed, lower, upper.
        When booking_type changes from multiple→single, we reset constraints.
        """
        # pick booking type from payload or existing instance
        booking = data.get('booking_type', getattr(self.instance, 'booking_type', None))
        fixed = data.get('fixed', getattr(self.instance, 'fixed', None))
        lower = data.get('lower_limit', getattr(self.instance, 'lower_limit', None))
        upper = data.get('upper_limit', getattr(self.instance, 'upper_limit', None))

        # ---- AUTO-RESET stale values when switching to single ----
        if booking == 'single':
            data['fixed'] = False
            data['lower_limit'] = None
            data['upper_limit'] = None
            return data

        # ---- Multiple + Fixed ----
        if booking == 'multiple' and fixed:
            if upper is None:
                raise serializers.ValidationError("Upper limit required when fixed")
            if lower is not None:
                raise serializers.ValidationError("Lower limit must be null when fixed")

        # ---- Multiple + Not Fixed ----
        if booking == 'multiple' and not fixed:
            if lower is None or upper is None:
                raise serializers.ValidationError("Lower & Upper limit both required")

        return data



class EventDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventDetails
        fields = [
            'id', 'event',
            'description', 'venue',
            'start_datetime', 'end_datetime'
        ]



class TempBookSerializer(serializers.ModelSerializer):
    class Meta:
        model = TempBook
        fields = ["id", "cart_item", "name", "email", "phone_number"]
        extra_kwargs = {
            "email": {"required": False, "allow_null": True, "allow_blank": True},
            "phone_number": {"required": False, "allow_null": True, "allow_blank": True},
        }

# events/serializers.py

class TempBookTimeslotSerializer(serializers.ModelSerializer):
    event_id = serializers.IntegerField(source="slot.event_id", read_only=True)
    event_name = serializers.CharField(source="slot.event.name", read_only=True)
    # NEW: SerializerMethodField to provide date and time details
    slot_info = serializers.SerializerMethodField()

    class Meta:
        model = TempBookTimeslot
        # Added slot_info to the fields list
        fields = ["id", "cart_item", "slot", "event_id", "event_name", "slot_info"]

    def get_slot_info(self, obj):
        """
        Extracts formatted date and time from the linked EventSlot.
        """
        return {
            "date": obj.slot.date,
            "start_time": obj.slot.start_time.strftime("%H:%M"),
            "end_time": obj.slot.end_time.strftime("%H:%M"),
        }

    def validate(self, data):
        cart_item = data.get("cart_item", getattr(self.instance, "cart_item", None))
        slot = data.get("slot", getattr(self.instance, "slot", None))

        if cart_item and slot and slot.event_id != cart_item.event_id:
            raise serializers.ValidationError("Slot must belong to the same event as the cart item.")

        # Capacity rule for non-unlimited slots
        if cart_item and slot and not slot.unlimited_participants:
            needed = cart_item.participants_count
            if slot.available_participants is None or slot.available_participants < needed:
                raise serializers.ValidationError("Selected slot does not have enough available capacity.")
        
        return data


class CartItemSerializer(serializers.ModelSerializer):
    event_name = serializers.CharField(source="event.name", read_only=True)
    event_price = serializers.DecimalField(source="event.price", max_digits=9, decimal_places=2, read_only=True)
    temp_participants = TempBookSerializer(many=True, read_only=True)
    temp_timeslot = TempBookTimeslotSerializer(read_only=True)

    class Meta:
        model = CartItem
        fields = [
    "id", "cart", "event", "event_name", "event_price",
    "participants_count", "created_at",
    "temp_participants", "temp_timeslot",
]

        read_only_fields = ["created_at"]

    def validate(self, data):
        # Defer to model.clean by constructing a temp instance
        instance = self.instance or CartItem(**data)
        if self.instance:
            for k, v in data.items():
                setattr(instance, k, v)
        # This will raise ValidationError if rules fail
        instance.clean()
        return data


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    
    class Meta:
        model = Cart
        fields = ["id", "owner", "is_active", "created_at", "items"]
        read_only_fields = ["owner", "created_at"]




class BookedParticipantSerializer(serializers.ModelSerializer):
    qr_token = serializers.UUIDField(read_only=True)

    class Meta:
        model = BookedParticipant
        fields = [
            "id",
            "booking",
            "booked_event",
            "name",
            "email",
            "phone_number",
            "arrived",
            "checkin_time",
            "qr_token",
        ]
        read_only_fields = [
            "booking",
            "booked_event",
            "arrived",
            "checkin_time",
            "qr_token",
        ]

class BookedEventSerializer(serializers.ModelSerializer):
    event_name = serializers.CharField(source="event.name", read_only=True)
    slot_info = serializers.SerializerMethodField()
    participants = BookedParticipantSerializer(many=True, read_only=True)

    class Meta:
        model = BookedEvent
        fields = [
            "id",
            "booking",
            "event",
            "event_name",
            "slot",
            "slot_info",
            "participants_count",
            "unit_price",
            "line_total",
            "participants",
        ]
        read_only_fields = ["booking", "unit_price", "line_total"]

    def get_slot_info(self, obj):
        return {
            "date": obj.slot.date,
            "start_time": obj.slot.start_time,
            "end_time": obj.slot.end_time,
            "unlimited": obj.slot.unlimited_participants,
        }


class BookingSerializer(serializers.ModelSerializer):
    booked_events = BookedEventSerializer(many=True, read_only=True)

    class Meta:
        model = Booking
        fields = ["id", "user", "created_at", "payment_status", "status", "total_amount", "booked_events"]
        read_only_fields = ["user", "created_at", "total_amount", "status"]


class BookedParticipantCheckinSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookedParticipant
        fields = ["arrived"]


class EventAnalyticsSerializer(serializers.Serializer):
    overview = serializers.DictField()
    registration_timeline = serializers.DictField()
    ticket_analytics = serializers.DictField()
    slot_analytics = serializers.ListField()
    revenue_analytics = serializers.DictField()
    attendance_analytics = serializers.DictField()
    participant_insights = serializers.DictField()
    organiser_analytics = serializers.DictField()
    event_status = serializers.DictField()
    top_statistics = serializers.DictField()
    event_header = serializers.DictField()


