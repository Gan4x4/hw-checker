from uuid import UUID

from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_safe

from .models import APIClient, Test
from .views import feedback_payload, results_csv_response


def api_json(payload, status=200):
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@require_safe
def tests(request, test_id=None, export=None):
    authorization = request.headers.get("Authorization", "").split()
    client = None
    if len(authorization) == 2 and authorization[0].lower() == "bearer":
        try:
            token = UUID(authorization[1])
        except ValueError:
            pass
        else:
            client = APIClient.objects.filter(token=token, is_active=True).first()
    if client is None:
        response = api_json({"error": "A valid client Bearer token is required."}, 401)
        response["WWW-Authenticate"] = "Bearer"
        return response

    queryset = Test.objects.all()
    if test_id is not None:
        if test_id > 9223372036854775807:
            return api_json({"error": "Test not found."}, 404)
        test = queryset.filter(pk=test_id).first()
        if test is None:
            return api_json({"error": "Test not found."}, 404)
        if export == "csv":
            return results_csv_response(test)
        return api_json(feedback_payload(test))

    if "test_ids" in request.GET:
        try:
            ids = {int(value) for value in request.GET["test_ids"].split(",")}
            if any(value <= 0 or value > 9223372036854775807 for value in ids):
                raise ValueError
        except ValueError:
            return api_json({"error": "test_ids must be a comma-separated list of positive test IDs."}, 400)
        queryset = queryset.filter(pk__in=ids)
        if queryset.count() != len(ids):
            return api_json({"error": "Test not found."}, 404)
    queryset = queryset.order_by("pk")
    if export == "feedback":
        # ponytail: batch exports fit in memory; paginate or stream if test volume outgrows one response.
        return api_json({"schema_version": 1, "tests": [feedback_payload(test) for test in queryset]})
    return api_json({"tests": [{
        "id": test.pk, "title": test.title, "academic_year": test.academic_year,
        "state": test.refresh_state(commit=False),
        "feedback_url": reverse("quiz:api_test_feedback", args=[test.pk]),
        "results_url": reverse("quiz:api_test_results", args=[test.pk]),
    } for test in queryset]})
