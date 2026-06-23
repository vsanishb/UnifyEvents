from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import viewsets, status
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, viewsets
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import *
from .serializers import *


from django.http import JsonResponse

def health_check(request):
    return JsonResponse({"status": "ok"})


class EventViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EventSerializer

    # IMPORTANT — restore base queryset so router works
    queryset = Event.objects.all()

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['parent_event']
    parser_classes = [JSONParser, MultiPartParser, FormParser]


    # ⭐ ALWAYS annotate and prefetch required relations
    def get_base_queryset(self):
        return (
            Event.objects
            .select_related(
                "constraint",
                "details",
                "parent_event",
                "category",
            )
            .prefetch_related("slots", "organisers")
        )

    def get_queryset(self):
        qs = self.get_base_queryset()
        user = self.request.user

        # If somehow unauthenticated
        if not user.is_authenticated:
            return Event.objects.none()

        # Admin sees all
        if user.role == 'admin':
            return qs

        # Organiser sees only their events
        if user.role == 'organiser':
            return qs.filter(organisers__user=user)

        # Participant should NOT use /events/
        # They must use /events/browse/
        return Event.objects.none()



    # ---------------------------
    # /events/browse/
    # ---------------------------
    @action(detail=False, methods=['get'], url_path='browse', permission_classes=[IsAuthenticated])
    def browse(self, request):
        user = request.user
        qs = self.get_base_queryset()

        # organisers cannot participate in their own events
        if user.role == 'organiser':
            qs = qs.exclude(organisers__user=user)

        # optional parent filter
        parent_event = request.query_params.get("parent_event")
        if parent_event:
            qs = qs.filter(parent_event=parent_event)

        # only fully configured events
        qs = qs.filter(
            constraint__isnull=False,
            details__isnull=False,
            slots__isnull=False,
        ).distinct()

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return super().update(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response({"detail": "Only admin can create events"}, status=status.HTTP_403_FORBIDDEN)
        return super().create(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response({"detail": "Only admin can delete events"}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'], url_path='attendance', permission_classes=[IsAuthenticated])
    def attendance(self, request, pk=None):
        import hashlib
        from django.db.models import Count, Q, F
        from rest_framework.pagination import PageNumberPagination

        user = request.user
        if user.role not in ['admin', 'organiser']:
            return Response({"detail": "You do not have permission to perform this action."}, status=status.HTTP_403_FORBIDDEN)

        try:
            event = Event.objects.get(id=pk)
        except Event.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if user.role == 'organiser' and not event.organisers.filter(user=user).exists():
            return Response({"detail": "You do not have permission to access this event's attendance records."}, status=status.HTTP_403_FORBIDDEN)

        qs = BookedEvent.objects.filter(event=event).select_related('booking', 'slot').prefetch_related('participants', 'participants__scanned_by__user')

        qs = qs.annotate(
            total_participants_count=Count('participants'),
            checked_in_participants_count=Count('participants', filter=Q(participants__arrived=True))
        )

        search_query = request.query_params.get('search')
        if search_query:
            participants_qs = BookedParticipant.objects.filter(booked_event__event=event)
            db_matches = participants_qs.filter(
                Q(name__icontains=search_query) |
                Q(email__icontains=search_query) |
                Q(phone_number__icontains=search_query)
            )
            matching_booked_event_ids = set(db_matches.values_list('booked_event_id', flat=True))

            all_parts = participants_qs.values('id', 'name', 'email', 'booked_event_id')
            for p in all_parts:
                seed = f"{p['name'] or ''}-{p['email'] or ''}-{p['id']}"
                digest = hashlib.md5(seed.encode('utf-8')).hexdigest()
                h = int(digest, 16)
                DEPARTMENTS = ["Computer Science", "Electrical Eng", "Mechanical Eng", "Information Tech", "Civil Eng", "Chemical Eng"]
                dept = DEPARTMENTS[h % len(DEPARTMENTS)]
                usn_num = (h % 200) + 1
                usn_dept = dept[:2].upper() if len(dept) >= 2 else "CS"
                if usn_dept == "CO":
                    usn_dept = "CS"
                usn = f"1RV22{usn_dept}{usn_num:03d}"

                if search_query.lower() in usn.lower():
                    matching_booked_event_ids.add(p['booked_event_id'])

            qs = qs.filter(id__in=matching_booked_event_ids)

        booking_type = request.query_params.get('type')
        if booking_type == 'single':
            qs = qs.filter(total_participants_count=1)
        elif booking_type == 'team':
            qs = qs.filter(total_participants_count__gt=1)

        status_param = request.query_params.get('status')
        if status_param == 'fully':
            qs = qs.filter(checked_in_participants_count=F('total_participants_count'), total_participants_count__gt=0)
        elif status_param == 'partially':
            qs = qs.filter(checked_in_participants_count__gt=0, checked_in_participants_count__lt=F('total_participants_count'))
        elif status_param == 'not':
            qs = qs.filter(checked_in_participants_count=0)

        ordering = request.query_params.get('ordering', '-newest')
        if ordering == 'oldest' or ordering == 'booking__created_at':
            qs = qs.order_by('booking__created_at')
        else:
            qs = qs.order_by('-booking__created_at')

        class AttendancePagination(PageNumberPagination):
            page_size = 10
            page_size_query_param = 'page_size'
            max_page_size = 100

        paginator = AttendancePagination()
        page = paginator.paginate_queryset(qs, request)

        if page is not None:
            serializer = BookingGroupSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = BookingGroupSerializer(qs, many=True)
        return Response(serializer.data)




from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status

from .models import Event
from app.utils.r2 import generate_signed_url
# events/views.py

from rest_framework.views import APIView
from rest_framework import permissions, status
from rest_framework.response import Response

from .models import Event
from app.utils.r2 import generate_signed_url


class SecureEventImageView(APIView):
    """
    Secure gateway for Event images stored in Cloudflare R2.
    - Requires authentication
    - Validates the image belongs to an Event
    - Returns short-lived signed URL
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        key = request.query_params.get("key")
        if not key:
            return Response({"error": "Missing key"}, status=400)


        allowed = Event.objects.filter(image__name=key).exists()



        if not allowed:
            return Response({"error": "Forbidden"}, status=403)

        signed_url = generate_signed_url(key=key, expires=300)

        return Response({
            "url": signed_url,
            "expires_in": 300,
        })



# views.py
from rest_framework.permissions import SAFE_METHODS

class EventSlotViewSet(viewsets.ModelViewSet):
    queryset = EventSlot.objects.all()
    serializer_class = EventSlotSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['event', 'date', 'available']

    def get_queryset(self):
        qs = EventSlot.objects.all()
        event_id = self.request.query_params.get("event_id")
        date = self.request.query_params.get("date")
        if event_id:
            qs = qs.filter(event_id=event_id)
        if date:
            qs = qs.filter(date=date)

        user = self.request.user

        # READ: allow participant & organiser to see all
        if self.request.method in SAFE_METHODS:
            return qs

        # WRITE: admin all; organiser only their events
        if user.role == "admin":
            return qs
        if user.role == "organiser":
            return qs.filter(event__organisers__user=user)
        return EventSlot.objects.none()



class CategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class ParentEventViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = ParentEvent.objects.all()
    serializer_class = ParentEventSerializer



from urllib.parse import unquote
from urllib.parse import unquote

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions

from .models import Event
from .utils.r2 import generate_signed_url


class SecureEventImageView(APIView):
    """
    The ONLY way event images are accessed.
    - Requires JWT authentication
    - Validates event image key exists
    - Generates short-lived R2 signed URL
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        key = request.query_params.get("key")
        if not key:
            return Response({"error": "Missing key"}, status=400)

        key = unquote(key)

        # ───── ACCESS CONTROL ─────
        allowed = Event.objects.filter(image=key).exists()

        if not allowed:
            return Response({"error": "Forbidden"}, status=403)

        # Generate signed R2 URL
        signed_url = generate_signed_url(key=key, expires=300)

        return Response({
            "url": signed_url,
            "expires_in": 300
        })



class ParticipationConstraintViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ParticipationConstraintSerializer
    queryset = ParticipationConstraint.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['event']

    def create(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response({"detail": "Only admin can create constraints"}, status=403)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if request.user.role != 'admin' and request.user.role != 'organiser':
            kwargs['partial'] = True
            return Response({"detail": "No permission to update constraints"}, status=403)
        return super().update(request, *args, **kwargs)



    def get_queryset(self):
        user = self.request.user

        # everyone logged in can READ constraints
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return ParticipationConstraint.objects.all()

        # write access rules
        if user.role == 'admin':
            return ParticipationConstraint.objects.all()

        if user.role == 'organiser':
            return ParticipationConstraint.objects.filter(event__organisers__user=user)

        return ParticipationConstraint.objects.none()




class EventDetailsViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EventDetailsSerializer
    queryset = EventDetails.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['event']

    def get_queryset(self):
        user = self.request.user

        # READ operations
        if self.request.method in SAFE_METHODS:  # GET, HEAD, OPTIONS
            return EventDetails.objects.all()

        # WRITE operations
        if user.role == 'admin':
            return EventDetails.objects.all()

        if user.role == 'organiser':
            return EventDetails.objects.filter(event__organisers__user=user)

        return EventDetails.objects.none()




class OrganiserViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = Organiser.objects.all()
    serializer_class = OrganiserSerializer




class IsOwnerOnly(permissions.BasePermission):
    """Allow access only to the owner of the cart (and its related objects)."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        if isinstance(obj, Cart):
            return obj.owner_id == user.id
        if isinstance(obj, CartItem):
            return obj.cart.owner_id == user.id
        if isinstance(obj, TempBook):
            return obj.cart_item.cart.owner_id == user.id
        if isinstance(obj, TempBookTimeslot):
            return obj.cart_item.cart.owner_id == user.id
        return False


class CartViewSet(viewsets.ModelViewSet):
    """
    We expose list to return/create the user's active cart.
    - GET /cart/   -> return (and auto-create) the current user's active cart (single object)
    - POST /cart/  -> create an active cart if none (optional in this flow)
    """
    serializer_class = CartSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Users see only their carts
        return Cart.objects.filter(owner=self.request.user)

    def list(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(owner=request.user, is_active=True)
        serializer = self.get_serializer(cart)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        # Optional: ensure a single active cart
        cart, created = Cart.objects.get_or_create(owner=request.user, is_active=True)
        serializer = self.get_serializer(cart)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class CartItemViewSet(viewsets.ModelViewSet):
    serializer_class = CartItemSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOnly]

    def get_queryset(self):
        # Only items belonging to current user's carts
        return CartItem.objects.filter(cart__owner=self.request.user)

    def perform_create(self, serializer):
        cart = serializer.validated_data.get("cart", None)
        event = serializer.validated_data.get("event")
        user = self.request.user

        if cart is None:
            cart, _ = Cart.objects.get_or_create(owner=user, is_active=True)
        else:
            if cart.owner_id != user.id:
                raise permissions.PermissionDenied("You cannot add items to another user's cart.")

        # 🚫 organisers cannot add their own events
        if user.role == 'organiser' and Organiser.objects.filter(user=user, events=event).exists():
            raise ValidationError("Organisers cannot participate in their own event.")

        serializer.save(cart=cart)


class TempBookViewSet(viewsets.ModelViewSet):
    """
    Create one row per participant. Prevent exceeding participants_count.
    """
    serializer_class = TempBookSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOnly]

    def get_queryset(self):
        return TempBook.objects.filter(cart_item__cart__owner=self.request.user)

    def perform_create(self, serializer):
        cart_item = serializer.validated_data["cart_item"]
        if cart_item.cart.owner_id != self.request.user.id:
            raise permissions.PermissionDenied("Not your cart item.")
        # Enforce count limit
        current = cart_item.temp_participants.count()
        if current >= cart_item.participants_count:
            raise ValidationError("You have already provided all required participant details.")
        serializer.save()


class TempBookTimeslotViewSet(viewsets.ModelViewSet):
    serializer_class = TempBookTimeslotSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOnly]

    def get_queryset(self):
        return TempBookTimeslot.objects.filter(cart_item__cart__owner=self.request.user)

    def perform_create(self, serializer):
        cart_item = serializer.validated_data["cart_item"]
        if cart_item.cart.owner_id != self.request.user.id:
            raise permissions.PermissionDenied("Not your cart item.")

        # Business rule: slot must have capacity for the entire team if limited
        slot = serializer.validated_data["slot"]
        if slot.event_id != cart_item.event_id:
            raise ValidationError("Slot does not belong to this event.")
        if not slot.unlimited_participants:
            needed = cart_item.participants_count
            if slot.available_participants is None or slot.available_participants < needed:
                raise ValidationError("Selected slot does not have enough capacity.")

        serializer.save()




# events/views.py (append)

from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    Booking, BookedEvent, BookedParticipant,
    Cart, CartItem, TempBook, TempBookTimeslot, EventSlot
)
from .serializers import BookingSerializer


class IsBookingOwner(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id

from decimal import Decimal
from django.db import transaction
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status

from .models import (
    Booking, BookedEvent, BookedParticipant,
    Cart, EventSlot, TempBookTimeslot
)
from .serializers import BookingSerializer


class BookingViewSet(viewsets.ModelViewSet):
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Booking.objects.filter(user=self.request.user).order_by("-created_at")

    @action(detail=False, methods=["post"], url_path="place")
    @transaction.atomic
    def place(self, request):
        user = request.user

        # ─────────────────────────────
        # 1. LOAD ACTIVE CART
        # ─────────────────────────────
        try:
            cart = Cart.objects.select_related("owner").prefetch_related(
                "items__event",
                "items__temp_participants",
                "items__temp_timeslot__slot",
            ).get(owner=user, is_active=True)
        except Cart.DoesNotExist:
            return Response({"detail": "No active cart found."}, status=400)

        items = list(cart.items.all())
        if not items:
            return Response({"detail": "Cart is empty."}, status=400)

        # ─────────────────────────────
        # 2. VALIDATE ITEMS
        # ─────────────────────────────
        for it in items:
            # ✅ Safe temp_timeslot access
            try:
                temp_slot = it.temp_timeslot
            except TempBookTimeslot.DoesNotExist:
                return Response(
                    {"detail": f"Event '{it.event.name}' has no selected slot."},
                    status=400
                )

            # ✅ Participants validation
            if it.temp_participants.count() != it.participants_count:
                return Response(
                    {"detail": f"Event '{it.event.name}' participant details are incomplete."},
                    status=400
                )

        # ─────────────────────────────
        # 3. CAPACITY CHECK (LOCK SLOTS)
        # ─────────────────────────────
        slot_required = {}

        for it in items:
            temp_slot = it.temp_timeslot
            slot_id = temp_slot.slot_id
            slot_required[slot_id] = slot_required.get(slot_id, 0) + it.participants_count

        locked_slots = EventSlot.objects.select_for_update().filter(id__in=slot_required.keys())
        slots_by_id = {s.id: s for s in locked_slots}

        for sid, needed in slot_required.items():
            slot = slots_by_id.get(sid)

            # ✅ FIX: prevent KeyError crash
            if not slot:
                return Response({"detail": "Invalid slot selected."}, status=400)

            if not slot.unlimited_participants:
                if slot.available_participants is None or slot.available_participants < needed:
                    return Response(
                        {
                            "detail": f"Slot {slot.date} {slot.start_time}-{slot.end_time} "
                                      f"doesn't have enough capacity."
                        },
                        status=400
                    )

        # ─────────────────────────────
        # 4. CREATE BOOKING
        # ─────────────────────────────
        booking = Booking.objects.create(
            user=user,
            status="confirmed",
            payment_status=None,
            total_amount=Decimal(0)
        )

        total = Decimal(0)

        # ─────────────────────────────
        # 5. CREATE BOOKED EVENTS + PARTICIPANTS
        # ─────────────────────────────
        for it in items:
            temp_slot = it.temp_timeslot
            slot = slots_by_id.get(temp_slot.slot_id)

            # ✅ SAFE DECIMAL HANDLING
            unit_price = Decimal(it.event.price or 0)
            participants_count = Decimal(it.participants_count)
            line_total = unit_price * participants_count

            booked_event = BookedEvent.objects.create(
                booking=booking,
                event=it.event,
                slot=slot,
                participants_count=int(participants_count),
                unit_price=unit_price,
                line_total=line_total,
            )

            total += line_total

            # ✅ CREATE PARTICIPANTS SAFELY
            for p in it.temp_participants.all():
                BookedParticipant.objects.create(
                    booking=booking,
                    booked_event=booked_event,
                    name=p.name,
                    email=p.email if p.email else None,
                    phone_number=p.phone_number if p.phone_number else None,
                    arrived=False,
                    checkin_time=None,
                )

            # ✅ UPDATE SLOT CAPACITY
            if not slot.unlimited_participants:
                slot.booked_participants += int(participants_count)

            slot.save()

        # ─────────────────────────────
        # 6. FINALIZE BOOKING
        # ─────────────────────────────
        booking.total_amount = total
        booking.save(update_fields=["total_amount"])

        # deactivate cart
        cart.is_active = False
        cart.save(update_fields=["is_active"])

        # ─────────────────────────────
        # 7. RESPONSE
        # ─────────────────────────────
        serializer = BookingSerializer(booking)
        return Response(serializer.data, status=201)

class BookedParticipantViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    queryset = BookedParticipant.objects.all()

    def get_serializer_class(self):
        if self.action == "checkin":
            return BookedParticipantCheckinSerializer
        return BookedParticipantSerializer

    # permission check
    def has_permission_on_obj(self, request, participant):
        user = request.user
        if user.role == "admin":
            return True
        if user.role == "organiser":
            return participant.booked_event.event.organisers.filter(user=user).exists()
        return False

    # SINGLE VALID CHECKIN ENDPOINT (no duplicates)
    @action(detail=True, methods=["post"], url_path="checkin")
    def checkin(self, request, pk=None):
        participant = self.get_object()

        if not self.has_permission_on_obj(request, participant):
            return Response({"detail": "Not allowed"}, status=403)

        if participant.arrived:
            return Response({"detail": "Participant is already checked-in."}, status=status.HTTP_400_BAD_REQUEST)

        participant.arrived = True
        participant.checkin_time = timezone.now()
        participant.reversed_by = None
        participant.reversed_time = None
        
        if request.user.role == "organiser":
            try:
                organiser = Organiser.objects.get(user=request.user)
                participant.scanned_by = organiser
            except Organiser.DoesNotExist:
                pass
        else:
            participant.scanned_by = None
            
        participant.save(update_fields=["arrived", "checkin_time", "scanned_by", "reversed_by", "reversed_time"])

        return Response({"detail": "Checked-in"}, status=200)

    @action(detail=True, methods=["post"], url_path="reverse-checkin")
    def reverse_checkin(self, request, pk=None):
        participant = self.get_object()

        if not self.has_permission_on_obj(request, participant):
            return Response({"detail": "Not allowed"}, status=403)

        if not participant.arrived:
            return Response({"detail": "Participant is not checked-in yet."}, status=status.HTTP_400_BAD_REQUEST)

        participant.arrived = False
        participant.checkin_time = None
        participant.scanned_by = None
        
        if request.user.role == "organiser":
            try:
                organiser = Organiser.objects.get(user=request.user)
                participant.reversed_by = organiser
            except Organiser.DoesNotExist:
                pass
        else:
            participant.reversed_by = None
            
        participant.reversed_time = timezone.now()
        participant.save(update_fields=["arrived", "checkin_time", "scanned_by", "reversed_by", "reversed_time"])

        return Response({"detail": "Attendance reversed"}, status=200)




# --- at top with other imports ---
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response

# add near other viewsets
class BookedEventViewSet(viewsets.ReadOnlyModelViewSet):

    serializer_class = BookedEventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        # Admin sees ALL
        if user.role == "admin":
            return BookedEvent.objects.all()

        # Organiser sees:
        # 1) events they organised
        # 2) AND bookings they personally made
        if user.role == "organiser":
            return BookedEvent.objects.filter(
                models.Q(event__organisers__user=user) |
                models.Q(booking__user=user)
            ).distinct()

        # Participant sees ONLY their bookings
        return BookedEvent.objects.filter(booking__user=user)


from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from rest_framework.response import Response
from app.models import BookedParticipant, Organiser, Event, BookedEvent
from django.db.models import Sum, Count, Avg, Max, Q, F
from django.db.models.functions import TruncDay, ExtractHour, TruncMonth
from app.permissions import IsAdminOrAssignedOrganiser


class QRCheckinView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("qr_token")
        scanned_at = request.data.get("scanned_at")  # optional (offline support)

        if not token:
            return Response({
                "status": "error",
                "message": "Missing QR token"
            }, status=400)

        try:
            participant = BookedParticipant.objects.select_related(
                "booked_event__event"
            ).get(qr_token=token)
        except BookedParticipant.DoesNotExist:
            if request.user.is_authenticated and request.user.role == "organiser":
                Event.objects.filter(organisers__user=request.user).update(
                    invalid_qr_scans=F('invalid_qr_scans') + 1
                )
            return Response({
                "status": "invalid",
                "message": "Invalid QR"
            }, status=404)

        user = request.user

        # 🔐 Permission check
        if user.role == "organiser":
            allowed = participant.booked_event.event.organisers.filter(user=user).exists()
            if not allowed:
                event = participant.booked_event.event
                event.rejected_scans = F('rejected_scans') + 1
                event.save(update_fields=['rejected_scans'])
                return Response({
                    "status": "forbidden",
                    "message": "Not allowed"
                }, status=403)

        if user.role not in ["admin", "organiser"]:
            return Response({
                "status": "forbidden",
                "message": "Not allowed"
            }, status=403)

        # 🔁 IDEMPOTENT CHECK-IN
        if participant.qr_used or participant.arrived:
            return Response({
                "status": "already_checked_in",
                "participant_name": participant.name,
                "checkin_time": participant.checkin_time,
            }, status=200)

        # ✅ Mark attendance
        participant.arrived = True
        participant.qr_used = True
        participant.checkin_time = timezone.now()
        if user.role == "organiser":
            try:
                organiser = Organiser.objects.get(user=user)
                participant.scanned_by = organiser
            except Organiser.DoesNotExist:
                pass
        participant.save(update_fields=["arrived", "qr_used", "checkin_time", "scanned_by"])

        return Response({
            "status": "checked_in",
            "participant_name": participant.name,
            "checkin_time": participant.checkin_time,
        }, status=200)


class QRPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("qr_token")

        if not token:
            return Response({
                "status": "error",
                "message": "Missing QR token"
            }, status=400)

        try:
            participant = BookedParticipant.objects.select_related(
                "booked_event__event",
                "booked_event__slot"
            ).get(qr_token=token)
        except BookedParticipant.DoesNotExist:
            if request.user.role == "organiser":
                Event.objects.filter(organisers__user=request.user).update(
                    invalid_qr_scans=F('invalid_qr_scans') + 1
                )
            return Response({
                "status": "invalid",
                "message": "Invalid QR"
            }, status=404)

        user = request.user

        # 🔐 Permission check
        if user.role == "organiser":
            allowed = participant.booked_event.event.organisers.filter(user=user).exists()
            if not allowed:
                event = participant.booked_event.event
                event.rejected_scans = F('rejected_scans') + 1
                event.save(update_fields=['rejected_scans'])
                return Response({
                    "status": "forbidden",
                    "message": "Not allowed"
                }, status=403)

        if user.role not in ["admin", "organiser"]:
            return Response({
                "status": "forbidden",
                "message": "Not allowed"
            }, status=403)

        slot = participant.booked_event.slot

        # 🔁 Already checked-in (clean response)
        if participant.qr_used or participant.arrived:
            return Response({
                "status": "already_checked_in",
                "participant_name": participant.name,
                "event_name": participant.booked_event.event.name,
                "slot": f"{slot.date} | {slot.start_time} - {slot.end_time}",
                "checkin_time": participant.checkin_time,
            }, status=200)

        # ✅ Normal preview
        return Response({
            "status": "valid",
            "participant_name": participant.name,
            "event_name": participant.booked_event.event.name,
            "slot": f"{slot.date} | {slot.start_time} - {slot.end_time}",
            "qr_token": str(participant.qr_token)
        }, status=200)


class EventAnalyticsAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrAssignedOrganiser]

    def get(self, request, event_id):
        # 1. Fetch Event (Eager loading relationships)
        try:
            event = Event.objects.select_related(
                'constraint', 'details', 'category', 'parent_event'
            ).prefetch_related('slots', 'organisers').get(id=event_id)
        except Event.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # Enforce permission checks
        self.check_object_permissions(request, event)

        now = timezone.now()

        # Helpers
        details = getattr(event, 'details', None)
        constraint = getattr(event, 'constraint', None)
        slots = event.slots.all()

        start_time = details.start_datetime if details else None
        end_time = details.end_datetime if details else None

        # Event status calculations
        if start_time and end_time:
            if now < start_time:
                status_str = "Upcoming"
            elif start_time <= now <= end_time:
                status_str = "Ongoing"
            else:
                status_str = "Completed"
        else:
            status_str = "Upcoming"

        days_remaining = 0
        if start_time and now < start_time:
            days_remaining = (start_time - now).days

        # Capacity logic
        total_capacity = 0
        is_unlimited = False
        remaining_capacity = 0
        for slot in slots:
            if slot.unlimited_participants:
                is_unlimited = True
            else:
                total_capacity += slot.max_participants or 0
                remaining_capacity += slot.available_participants or 0

        capacity_str = "unlimited" if is_unlimited else str(total_capacity)
        remaining_capacity_str = "Unlimited" if is_unlimited else str(remaining_capacity)
        capacity_full = False if is_unlimited else (remaining_capacity <= 0)
        
        registration_open = (now <= end_time) if end_time else True
        if capacity_full:
            registration_open = False

        # Bookings & Registrations query
        booked_events_qs = BookedEvent.objects.filter(event=event)
        confirmed_bookings_qs = booked_events_qs.filter(booking__status='confirmed')
        cancelled_bookings_qs = booked_events_qs.filter(booking__status='cancelled')

        participants_qs = BookedParticipant.objects.filter(booked_event__event=event)
        confirmed_participants_qs = participants_qs.filter(booked_event__booking__status='confirmed')
        checked_in_participants_qs = participants_qs.filter(arrived=True)
        not_checked_in_participants_qs = participants_qs.filter(arrived=False)

        total_registrations = confirmed_participants_qs.count()
        attendance_count = checked_in_participants_qs.count()
        attendance_percentage = (attendance_count / total_registrations * 100) if total_registrations > 0 else 0.0

        # Revenue
        revenue = confirmed_bookings_qs.aggregate(total=Sum('line_total'))['total'] or 0.0
        revenue = float(revenue)

        # Average booking size
        avg_booking_size = confirmed_bookings_qs.aggregate(avg=Avg('participants_count'))['avg'] or 0.0

        # Registration timeline last 30 days
        thirty_days_ago = now - timezone.timedelta(days=30)
        daily_reg_qs = (
            confirmed_participants_qs.filter(booked_event__booking__created_at__gte=thirty_days_ago)
            .annotate(day=TruncDay('booked_event__booking__created_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('day')
        )
        
        daily_reg_map = {item['day'].strftime('%Y-%m-%d'): item['count'] for item in daily_reg_qs if item['day']}
        last_30_days_list = []
        for i in range(30):
            d = (now - timezone.timedelta(days=29 - i)).strftime('%Y-%m-%d')
            last_30_days_list.append({
                "date": d,
                "count": daily_reg_map.get(d, 0)
            })

        last_7_days_list = last_30_days_list[-7:]
        today_str = now.strftime('%Y-%m-%d')
        today_reg_count = daily_reg_map.get(today_str, 0)

        monthly_reg_qs = (
            confirmed_participants_qs.annotate(month=TruncMonth('booked_event__booking__created_at'))
            .values('month')
            .annotate(count=Count('id'))
            .order_by('month')
        )
        monthly_registrations = [
            {"month": item['month'].strftime('%Y-%m') if item['month'] else "", "count": item['count']}
            for item in monthly_reg_qs
        ]

        peak_day_qs = (
            confirmed_participants_qs.annotate(day=TruncDay('booked_event__booking__created_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        peak_registration_day = peak_day_qs[0]['day'].strftime('%Y-%m-%d') if peak_day_qs.exists() and peak_day_qs[0]['day'] else None

        peak_hour_qs = (
            confirmed_participants_qs.annotate(hour=ExtractHour('booked_event__booking__created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        peak_registration_hour = peak_hour_qs[0]['hour'] if peak_hour_qs.exists() else None

        # Ticket analytics
        single_bookings = confirmed_bookings_qs.filter(participants_count=1).count()
        team_bookings = confirmed_bookings_qs.filter(participants_count__gt=1).count()
        avg_team_size = confirmed_bookings_qs.filter(participants_count__gt=1).aggregate(avg=Avg('participants_count'))['avg'] or 0.0
        largest_team_size = confirmed_bookings_qs.aggregate(max_val=Max('participants_count'))['max_val'] or 0

        # Slot analytics
        slot_analytics = []
        for slot in slots:
            slot_booked = slot.booked_participants
            slot_capacity = slot.max_participants or 0
            slot_available = slot.available_participants or 0
            
            if slot.unlimited_participants:
                occupancy_pct = 0.0
            else:
                occupancy_pct = (slot_booked / slot_capacity * 100) if slot_capacity > 0 else 0.0

            slot_analytics.append({
                "id": slot.id,
                "date": slot.date.strftime('%Y-%m-%d'),
                "start_time": slot.start_time.strftime('%H:%M'),
                "end_time": slot.end_time.strftime('%H:%M'),
                "capacity": slot_capacity if not slot.unlimited_participants else None,
                "booked_count": slot_booked,
                "remaining_count": slot_available if not slot.unlimited_participants else None,
                "occupancy_percentage": occupancy_pct,
                "unlimited_badge": slot.unlimited_participants
            })

        sorted_slots_by_booked = sorted(slots, key=lambda s: s.booked_participants, reverse=True)
        most_popular_slot = f"{sorted_slots_by_booked[0].date} {sorted_slots_by_booked[0].start_time.strftime('%H:%M')}" if sorted_slots_by_booked else None
        least_popular_slot = f"{sorted_slots_by_booked[-1].date} {sorted_slots_by_booked[-1].start_time.strftime('%H:%M')}" if sorted_slots_by_booked else None

        # Revenue Analytics
        revenue_by_slot_qs = (
            confirmed_bookings_qs.values('slot__date', 'slot__start_time')
            .annotate(revenue=Sum('line_total'))
            .order_by('-revenue')
        )
        revenue_by_slot = []
        for item in revenue_by_slot_qs:
            slot_label = f"{item['slot__date']} {item['slot__start_time'].strftime('%H:%M')}" if item['slot__start_time'] else ""
            revenue_by_slot.append({
                "slot": slot_label,
                "revenue": float(item['revenue'] or 0.0)
            })

        revenue_by_day_qs = (
            confirmed_bookings_qs.filter(booking__created_at__gte=thirty_days_ago)
            .annotate(day=TruncDay('booking__created_at'))
            .values('day')
            .annotate(revenue=Sum('line_total'))
            .order_by('day')
        )
        revenue_by_day_map = {item['day'].strftime('%Y-%m-%d'): float(item['revenue'] or 0.0) for item in revenue_by_day_qs if item['day']}
        revenue_by_day = []
        for i in range(30):
            d = (now - timezone.timedelta(days=29 - i)).strftime('%Y-%m-%d')
            revenue_by_day.append({
                "date": d,
                "revenue": revenue_by_day_map.get(d, 0.0)
            })

        paid_bookings = confirmed_bookings_qs.filter(unit_price__gt=0).count()
        free_bookings = confirmed_bookings_qs.filter(unit_price=0).count()
        highest_booking_value = confirmed_bookings_qs.aggregate(max_val=Max('line_total'))['max_val'] or 0.0
        highest_booking_value = float(highest_booking_value)
        average_booking_value = confirmed_bookings_qs.aggregate(avg=Avg('line_total'))['avg'] or 0.0
        average_booking_value = float(average_booking_value)

        # Attendance Analytics
        checked_in_participants = attendance_count
        not_checked_in_participants = not_checked_in_participants_qs.count()
        invalid_qr_scans = event.invalid_qr_scans
        rejected_scans = event.rejected_scans

        peak_checkin_qs = (
            participants_qs.filter(arrived=True)
            .annotate(hour=ExtractHour('checkin_time'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        peak_checkin_hour = peak_checkin_qs[0]['hour'] if peak_checkin_qs.exists() else None

        # Participant Insights
        total_participants = confirmed_participants_qs.count()
        unique_participants = confirmed_participants_qs.values('email').distinct().count()

        repeat_emails_qs = (
            confirmed_participants_qs.values('email')
            .annotate(cnt=Count('id'))
            .filter(cnt__gt=1)
        )
        repeat_participants = repeat_emails_qs.count()

        # Deterministic mock distributions
        DEPARTMENTS = ["Computer Science", "Electrical Eng", "Mechanical Eng", "Information Tech", "Civil Eng", "Chemical Eng"]
        YEARS = ["1st Year", "2nd Year", "3rd Year", "4th Year"]

        dept_dist = {}
        year_dist = {}
        for p in confirmed_participants_qs:
            seed_str = p.email or p.name or str(p.id)
            h = hash(seed_str)
            dept = DEPARTMENTS[h % len(DEPARTMENTS)]
            year = YEARS[h % len(YEARS)]
            dept_dist[dept] = dept_dist.get(dept, 0) + 1
            year_dist[year] = year_dist.get(year, 0) + 1

        dept_distribution = [{"department": d, "count": c} for d, c in dept_dist.items()]
        year_distribution = [{"year": y, "count": c} for y, c in year_dist.items()]
        most_common_year = max(year_dist.keys(), key=lambda k: year_dist[k]) if year_dist else None

        # Organiser analytics
        assigned_organisers = [org.user.username for org in event.organisers.select_related('user').all()]
        attendance_scans_performed = participants_qs.filter(scanned_by__isnull=False).count()
        
        organiser_activity = []
        for org in event.organisers.select_related('user').all():
            scans_count = participants_qs.filter(scanned_by=org).count()
            organiser_activity.append({
                "name": org.user.username,
                "scans": scans_count
            })

        # Top Statistics
        peak_booking_hour_qs = (
            confirmed_bookings_qs.annotate(hour=ExtractHour('booking__created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        peak_booking_hour = peak_booking_hour_qs[0]['hour'] if peak_booking_hour_qs.exists() else None

        peak_booking_day_qs = (
            confirmed_bookings_qs.annotate(day=TruncDay('booking__created_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        peak_booking_day = peak_booking_day_qs[0]['day'].strftime('%Y-%m-%d') if peak_booking_day_qs.exists() and peak_booking_day_qs[0]['day'] else None

        slot_attendance_qs = (
            participants_qs.filter(arrived=True)
            .values('booked_event__slot__date', 'booked_event__slot__start_time')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        highest_attendance_slot = None
        if slot_attendance_qs.exists() and slot_attendance_qs[0]['booked_event__slot__start_time']:
            highest_attendance_slot = f"{slot_attendance_qs[0]['booked_event__slot__date']} {slot_attendance_qs[0]['booked_event__slot__start_time'].strftime('%H:%M')}"

        lowest_attendance_qs = (
            participants_qs.filter(arrived=True)
            .values('booked_event__slot__date', 'booked_event__slot__start_time')
            .annotate(count=Count('id'))
            .order_by('count')
        )
        lowest_attendance_slot = None
        if lowest_attendance_qs.exists() and lowest_attendance_qs[0]['booked_event__slot__start_time']:
            lowest_attendance_slot = f"{lowest_attendance_qs[0]['booked_event__slot__date']} {lowest_attendance_qs[0]['booked_event__slot__start_time'].strftime('%H:%M')}"

        highest_revenue_slot_str = None
        if revenue_by_slot:
            highest_revenue_slot_str = revenue_by_slot[0]['slot']

        # Event Header
        event_header = {
            "name": event.name,
            "image_key": event.image.name if event.image else None,
            "parent_event_name": event.parent_event.name if event.parent_event else None,
            "category_name": event.category.name if event.category else None,
            "venue": details.venue if details else "",
            "date": start_time.strftime('%Y-%m-%d') if start_time else "",
            "status": status_str,
            "capacity": capacity_str,
        }

        # Build payload
        payload = {
            "event_header": event_header,
            "overview": {
                "total_registrations": total_registrations,
                "confirmed_bookings": confirmed_bookings_qs.count(),
                "cancelled_bookings": cancelled_bookings_qs.count(),
                "attendance_count": attendance_count,
                "attendance_percentage": attendance_percentage,
                "remaining_capacity": remaining_capacity_str,
                "revenue": revenue,
                "average_booking_size": avg_booking_size,
            },
            "registration_timeline": {
                "today": today_reg_count,
                "last_7_days": last_7_days_list,
                "last_30_days": last_30_days_list,
                "monthly_registrations": monthly_registrations,
                "peak_registration_day": peak_registration_day,
                "peak_registration_hour": peak_registration_hour,
            },
            "ticket_analytics": {
                "single_participant_bookings": single_bookings,
                "team_bookings": team_bookings,
                "average_team_size": avg_team_size,
                "largest_team_size": largest_team_size,
                "fixed_team_events": constraint.fixed if constraint else False,
                "flexible_team_events": not constraint.fixed if (constraint and constraint.booking_type == 'multiple') else False,
            },
            "slot_analytics": slot_analytics,
            "revenue_analytics": {
                "total_revenue": revenue,
                "revenue_by_slot": revenue_by_slot,
                "revenue_by_day": revenue_by_day,
                "paid_bookings": paid_bookings,
                "free_bookings": free_bookings,
                "highest_booking_value": highest_booking_value,
                "average_booking_value": average_booking_value,
            },
            "attendance_analytics": {
                "checked_in_participants": checked_in_participants,
                "not_checked_in_participants": not_checked_in_participants,
                "attendance_percentage": attendance_percentage,
                "invalid_qr_scans": invalid_qr_scans,
                "rejected_scans": rejected_scans,
                "peak_check_in_hour": peak_checkin_hour,
            },
            "participant_insights": {
                "total_participants": total_participants,
                "unique_participants": unique_participants,
                "repeat_participants": repeat_participants,
                "department_distribution": dept_distribution,
                "year_distribution": year_distribution,
                "most_common_year": most_common_year,
                "average_team_size": avg_booking_size,
            },
            "organiser_analytics": {
                "assigned_organisers": assigned_organisers,
                "attendance_scans_performed": attendance_scans_performed,
                "organiser_activity": organiser_activity,
            },
            "event_status": {
                "upcoming": status_str == "Upcoming",
                "ongoing": status_str == "Ongoing",
                "completed": status_str == "Completed",
                "days_remaining": days_remaining,
                "registration_open": registration_open,
                "capacity_full": capacity_full,
            },
            "top_statistics": {
                "peak_booking_hour": peak_booking_hour,
                "peak_booking_day": peak_booking_day,
                "highest_attendance_slot": highest_attendance_slot,
                "lowest_attendance_slot": lowest_attendance_slot,
                "highest_revenue_slot": highest_revenue_slot_str,
            }
        }

        serializer = EventAnalyticsSerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)