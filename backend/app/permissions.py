from rest_framework import permissions

class IsAdminOrAssignedOrganiser(permissions.BasePermission):
    """
    Permission check to verify if the requesting user is either:
    1. An Admin.
    2. An Organiser explicitly assigned to the event.
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        # obj is the Event
        if request.user.role == 'admin':
            return True
        if request.user.role == 'organiser':
            return obj.organisers.filter(user=request.user).exists()
        return False
