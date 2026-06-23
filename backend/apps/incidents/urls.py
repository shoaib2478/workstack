from django.urls import path

from apps.incidents.views import TriageRunCheckpointsView, TriageRunStreamView

urlpatterns = [
    path("runs/<str:run_id>/events/", TriageRunCheckpointsView.as_view(), name="triage-run-events"),
    path("runs/<str:run_id>/stream/", TriageRunStreamView.as_view(), name="triage-run-stream"),
]
