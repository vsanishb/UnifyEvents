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

