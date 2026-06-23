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
    college = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()
    usn = serializers.SerializerMethodField()
    checked_in = serializers.BooleanField(source='arrived', read_only=True)
    checked_in_at = serializers.DateTimeField(source='checkin_time', read_only=True)
    checked_in_by = serializers.CharField(source='scanned_by.user.username', read_only=True, allow_null=True)
    qr_status = serializers.SerializerMethodField()

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
            "college",
            "department",
            "year",
            "usn",
            "checked_in",
            "checked_in_at",
            "checked_in_by",
            "qr_status",
        ]
        read_only_fields = [
            "booking",
            "booked_event",
            "arrived",
            "checkin_time",
            "qr_token",
        ]

    def _get_deterministic_hash(self, obj):
        import hashlib
        seed = f"{obj.name or ''}-{obj.email or ''}-{obj.id}"
        digest = hashlib.md5(seed.encode('utf-8')).hexdigest()
        return int(digest, 16)

    def get_college(self, obj):
        h = self._get_deterministic_hash(obj)
        COLLEGES = ["RV College of Engineering", "PES University", "BMS College of Engineering", "MSRIT", "BIT"]
        return COLLEGES[h % len(COLLEGES)]

    def get_department(self, obj):
        h = self._get_deterministic_hash(obj)
        DEPARTMENTS = ["Computer Science", "Electrical Eng", "Mechanical Eng", "Information Tech", "Civil Eng", "Chemical Eng"]
        return DEPARTMENTS[h % len(DEPARTMENTS)]

    def get_year(self, obj):
        h = self._get_deterministic_hash(obj)
        YEARS = ["1st Year", "2nd Year", "3rd Year", "4th Year"]
        return YEARS[h % len(YEARS)]

    def get_usn(self, obj):
        h = self._get_deterministic_hash(obj)
        DEPARTMENTS = ["Computer Science", "Electrical Eng", "Mechanical Eng", "Information Tech", "Civil Eng", "Chemical Eng"]
        dept = DEPARTMENTS[h % len(DEPARTMENTS)]
        usn_num = (h % 200) + 1
        usn_dept = dept[:2].upper() if len(dept) >= 2 else "CS"
        if usn_dept == "CO":
            usn_dept = "CS"
        return f"1RV22{usn_dept}{usn_num:03d}"

    def get_qr_status(self, obj):
        return "Used" if obj.qr_used else "Pending"

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


class BookedParticipantAttendanceSerializer(serializers.ModelSerializer):
    checked_in = serializers.BooleanField(source='arrived')
    checked_in_at = serializers.DateTimeField(source='checkin_time', allow_null=True)
    checked_in_by = serializers.SerializerMethodField()
    qr_status = serializers.SerializerMethodField()
    booking_status = serializers.CharField(source='booking.status', read_only=True)
    college = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()
    usn = serializers.SerializerMethodField()

    class Meta:
        model = BookedParticipant
        fields = [
            "id",
            "name",
            "email",
            "phone_number",
            "college",
            "department",
            "year",
            "usn",
            "checked_in",
            "checked_in_at",
            "checked_in_by",
            "qr_status",
            "booking_status",
        ]

    def _get_hash_details(self, obj):
        if hasattr(obj, '_hash_details'):
            return obj._hash_details
        import hashlib
        seed = f"{obj.name or ''}-{obj.email or ''}-{obj.id}"
        digest = hashlib.md5(seed.encode('utf-8')).hexdigest()
        h = int(digest, 16)
        
        DEPARTMENTS = ["Computer Science", "Electrical Eng", "Mechanical Eng", "Information Tech", "Civil Eng", "Chemical Eng"]
        YEARS = ["1st Year", "2nd Year", "3rd Year", "4th Year"]
        COLLEGES = ["RV College of Engineering", "PES University", "BMS College of Engineering", "MSRIT", "BIT"]
        
        dept = DEPARTMENTS[h % len(DEPARTMENTS)]
        year = YEARS[h % len(YEARS)]
        college = COLLEGES[h % len(COLLEGES)]
        usn_num = (h % 200) + 1
        usn_dept = dept[:2].upper() if len(dept) >= 2 else "CS"
        if usn_dept == "CO":
            usn_dept = "CS"
        usn = f"1RV22{usn_dept}{usn_num:03d}"
        
        obj._hash_details = {
            "college": college,
            "department": dept,
            "year": year,
            "usn": usn
        }
        return obj._hash_details

    def get_college(self, obj):
        return self._get_hash_details(obj)["college"]

    def get_department(self, obj):
        return self._get_hash_details(obj)["department"]

    def get_year(self, obj):
        return self._get_hash_details(obj)["year"]

    def get_usn(self, obj):
        return self._get_hash_details(obj)["usn"]

    def get_checked_in_by(self, obj):
        return obj.scanned_by.user.username if obj.scanned_by else None

    def get_qr_status(self, obj):
        return "Used" if obj.qr_used else "Pending"


class BookingGroupSerializer(serializers.ModelSerializer):
    booked_event_id = serializers.IntegerField(source='id')
    booking_id = serializers.IntegerField(source='booking.id')
    booking_reference = serializers.SerializerMethodField()
    booking_time = serializers.DateTimeField(source='booking.created_at')
    slot = serializers.SerializerMethodField()
    participants = BookedParticipantAttendanceSerializer(many=True)
    total_participants = serializers.IntegerField(source='total_participants_count')
    checked_in_count = serializers.IntegerField(source='checked_in_participants_count')
    pending_count = serializers.SerializerMethodField()
    overall_status = serializers.SerializerMethodField()

    class Meta:
        model = BookedEvent
        fields = [
            "booked_event_id",
            "booking_id",
            "booking_reference",
            "booking_time",
            "slot",
            "participants",
            "total_participants",
            "checked_in_count",
            "pending_count",
            "overall_status",
        ]

    def get_booking_reference(self, obj):
        return f"#EVT-{obj.booking.id}"

    def get_slot(self, obj):
        slot = obj.slot
        if not slot:
            return ""
        date_str = slot.date.strftime('%Y-%m-%d') if slot.date else ""
        start_str = slot.start_time.strftime('%H:%M') if slot.start_time else ""
        end_str = slot.end_time.strftime('%H:%M') if slot.end_time else ""
        return f"{date_str} | {start_str} - {end_str}"

    def get_pending_count(self, obj):
        tot = getattr(obj, 'total_participants_count', 0)
        checked = getattr(obj, 'checked_in_participants_count', 0)
        return tot - checked

    def get_overall_status(self, obj):
        tot = getattr(obj, 'total_participants_count', 0)
        checked = getattr(obj, 'checked_in_participants_count', 0)
        if tot == 0:
            return "Not Attended"
        elif checked == tot:
            return "Fully Attended"
        elif checked > 0:
            return "Partially Attended"
        else:
            return "Not Attended"


