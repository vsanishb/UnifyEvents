from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone
from .models import Event, EventDetails, Organiser, EventSlot

User = get_user_model()

class EventAnalyticsTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        
        # Create users
        self.admin = User.objects.create_superuser(email="admin@test.com", password="password", role="admin")
        self.organiser1 = User.objects.create_user(email="org1@test.com", password="password", role="organiser")
        self.organiser2 = User.objects.create_user(email="org2@test.com", password="password", role="organiser")
        self.participant = User.objects.create_user(email="part@test.com", password="password", role="participant")
        
        # Create Organiser profiles
        self.org_profile1 = Organiser.objects.create(user=self.organiser1)
        self.org_profile2 = Organiser.objects.create(user=self.organiser2)
        
        # Create Event
        self.event = Event.objects.create(
            name="Test Event",
            parent_committee="Test Committee",
            price=10.00
        )
        self.event.organisers.add(self.org_profile1)
        
        # Event Details
        self.details = EventDetails.objects.create(
            event=self.event,
            description="Test Description",
            venue="Test Venue",
            start_datetime=timezone.now() + timezone.timedelta(days=1),
            end_datetime=timezone.now() + timezone.timedelta(days=2)
        )
        
        # Event Slot
        self.slot = EventSlot.objects.create(
            event=self.event,
            date=timezone.now().date(),
            start_time=timezone.now().time(),
            end_time=(timezone.now() + timezone.timedelta(hours=2)).time(),
            unlimited_participants=False,
            max_participants=10
        )

    def test_admin_access(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/events/{self.event.id}/analytics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("overview", response.data)
        self.assertIn("event_header", response.data)

    def test_assigned_organiser_access(self):
        self.client.force_authenticate(user=self.organiser1)
        response = self.client.get(f"/events/{self.event.id}/analytics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unassigned_organiser_access(self):
        self.client.force_authenticate(user=self.organiser2)
        response = self.client.get(f"/events/{self.event.id}/analytics/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_participant_access(self):
        self.client.force_authenticate(user=self.participant)
        response = self.client.get(f"/events/{self.event.id}/analytics/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_event_id(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/events/99999/analytics/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class EventAttendanceTestCase(EventAnalyticsTestCase):
    def setUp(self):
        super().setUp()
        from .models import Booking, BookedEvent, BookedParticipant
        
        # Create a booking
        self.booking = Booking.objects.create(
            user=self.participant,
            status="confirmed",
            payment_status="paid",
            total_amount=10.00
        )
        
        # Create a BookedEvent
        self.booked_event = BookedEvent.objects.create(
            booking=self.booking,
            event=self.event,
            slot=self.slot,
            participants_count=2,
            unit_price=10.00,
            line_total=20.00
        )
        
        # Create BookedParticipant 1 (Not Checked In)
        self.participant1 = BookedParticipant.objects.create(
            booking=self.booking,
            booked_event=self.booked_event,
            name="Alice Smith",
            email="alice@test.com",
            phone_number="1234567890",
            arrived=False
        )
        
        # Create BookedParticipant 2 (Checked In)
        self.participant2 = BookedParticipant.objects.create(
            booking=self.booking,
            booked_event=self.booked_event,
            name="Bob Jones",
            email="bob@test.com",
            phone_number="0987654321",
            arrived=True,
            checkin_time=timezone.now(),
            scanned_by=self.org_profile1
        )

    def test_attendance_access_control(self):
        # Admin - 200 OK
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/events/{self.event.id}/attendance/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Assigned Organiser - 200 OK
        self.client.force_authenticate(user=self.organiser1)
        response = self.client.get(f"/events/{self.event.id}/attendance/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Unassigned Organiser - 403 Forbidden
        self.client.force_authenticate(user=self.organiser2)
        response = self.client.get(f"/events/{self.event.id}/attendance/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Participant - 403 Forbidden
        self.client.force_authenticate(user=self.participant)
        response = self.client.get(f"/events/{self.event.id}/attendance/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_attendance_search_and_filters(self):
        self.client.force_authenticate(user=self.admin)

        # Basic list
        response = self.client.get(f"/events/{self.event.id}/attendance/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["booked_event_id"], self.booked_event.id)

        # Search by name
        response = self.client.get(f"/events/{self.event.id}/attendance/?search=Alice")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

        response = self.client.get(f"/events/{self.event.id}/attendance/?search=Nonexistent")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

        # Filter status partially
        response = self.client.get(f"/events/{self.event.id}/attendance/?status=partially")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

        # Filter status fully => 0
        response = self.client.get(f"/events/{self.event.id}/attendance/?status=fully")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

        # Filter type single => 0
        response = self.client.get(f"/events/{self.event.id}/attendance/?type=single")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

        # Filter type team => 1
        response = self.client.get(f"/events/{self.event.id}/attendance/?type=team")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_participant_checkin_and_reverse(self):
        self.client.force_authenticate(user=self.organiser1)

        # Check-in Alice (Not Checked In initially)
        response = self.client.post(f"/booked-participants/{self.participant1.id}/checkin/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.participant1.refresh_from_db()
        self.assertTrue(self.participant1.arrived)
        self.assertIsNotNone(self.participant1.checkin_time)
        self.assertEqual(self.participant1.scanned_by, self.org_profile1)

        # Attempt to check-in again -> 400 Bad Request
        response = self.client.post(f"/booked-participants/{self.participant1.id}/checkin/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Reverse check-in Bob (Checked In initially)
        response = self.client.post(f"/booked-participants/{self.participant2.id}/reverse-checkin/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.participant2.refresh_from_db()
        self.assertFalse(self.participant2.arrived)
        self.assertIsNone(self.participant2.checkin_time)
        self.assertIsNone(self.participant2.scanned_by)
        self.assertEqual(self.participant2.reversed_by, self.org_profile1)
        self.assertIsNotNone(self.participant2.reversed_time)

        # Attempt to reverse check-in again -> 400 Bad Request
        response = self.client.post(f"/booked-participants/{self.participant2.id}/reverse-checkin/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class OrganiserManagementTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Create users
        self.admin = User.objects.create_superuser(email="admin@test.com", password="password", role="admin")
        self.organiser = User.objects.create_user(email="org@test.com", password="password", role="organiser")
        self.participant = User.objects.create_user(email="part@test.com", password="password", role="participant")
        self.promotable = User.objects.create_user(email="promo@test.com", username="promo_user", password="password", role="participant")
        
        # Create profile
        self.org_profile = Organiser.objects.create(user=self.organiser)

        # Create event
        self.event = Event.objects.create(
            name="Organiser Test Event",
            parent_committee="Admin Committee",
            price=15.00
        )

    def test_organiser_list_permissions(self):
        # Admin - OK
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/organisers/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Organiser - Forbidden
        self.client.force_authenticate(user=self.organiser)
        response = self.client.get("/organisers/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Participant - Forbidden
        self.client.force_authenticate(user=self.participant)
        response = self.client.get("/organisers/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_organiser_list_permissions_and_search(self):
        # Admin - OK
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/organisers/non-organisers/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Search check
        response = self.client.get("/organisers/non-organisers/?search=promo")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if 'results' in response.data else response.data
        self.assertTrue(any(u['id'] == self.promotable.id for u in results))

        # Participant - Forbidden
        self.client.force_authenticate(user=self.participant)
        response = self.client.get("/organisers/non-organisers/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_promote_role(self):
        self.client.force_authenticate(user=self.admin)
        # Promote
        response = self.client.post("/organisers/promote/", {"user_id": self.promotable.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        self.promotable.refresh_from_db()
        self.assertEqual(self.promotable.role, 'organiser')
        self.assertTrue(Organiser.objects.filter(user=self.promotable).exists())

    def test_demote_role_with_event_assignment(self):
        self.client.force_authenticate(user=self.admin)
        # Assign organiser to event
        self.event.organisers.add(self.org_profile)

        # Try to demote -> 400 Bad Request
        response = self.client.post("/organisers/demote/", {"organiser_id": self.org_profile.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot remove organiser role", response.data["detail"])

        # Unassign from event
        self.event.organisers.remove(self.org_profile)

        # Demote -> 200 OK
        response = self.client.post("/organisers/demote/", {"organiser_id": self.org_profile.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.organiser.refresh_from_db()
        self.assertEqual(self.organiser.role, 'participant')
        self.assertFalse(Organiser.objects.filter(id=self.org_profile.id).exists())

    def test_event_update_organisers_security(self):
        # Organiser try to change organisers list on their event -> 403 Forbidden
        self.event.organisers.add(self.org_profile)
        self.client.force_authenticate(user=self.organiser)

        response = self.client.patch(f"/events/{self.event.id}/", {"organisers": []})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Admin change organisers list -> 200 OK
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(f"/events/{self.event.id}/", {"organisers": []})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.event.organisers.count(), 0)


