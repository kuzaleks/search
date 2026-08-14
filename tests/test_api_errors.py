import unittest

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.errors import APIError, install_error_handlers
from app.main import app


def make_error_test_app() -> FastAPI:
    test_app = FastAPI()
    install_error_handlers(test_app)

    @test_app.get("/expected")
    async def expected_error() -> None:
        raise APIError(409, "test_conflict", "Test conflict")

    @test_app.get("/unexpected")
    async def unexpected_error() -> None:
        raise RuntimeError("sensitive internal detail")

    @test_app.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return test_app


class APIErrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = AsyncClient(
            transport=ASGITransport(
                app=make_error_test_app(),
                raise_app_exceptions=False,
            ),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_expected_error_uses_shared_envelope(self) -> None:
        response = await self.client.get("/expected")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "test_conflict",
                    "message": "Test conflict",
                }
            },
        )

    async def test_validation_error_contains_safe_field_details(self) -> None:
        response = await self.client.get("/items/not-an-integer")

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["error"]["code"], "validation_error")
        self.assertEqual(
            body["error"]["message"],
            "Request validation failed",
        )
        self.assertEqual(
            body["error"]["details"][0]["field"],
            "path.item_id",
        )
        self.assertNotIn("not-an-integer", response.text)

    async def test_framework_http_error_uses_shared_envelope(self) -> None:
        response = await self.client.get("/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "not_found",
                    "message": "Not Found",
                }
            },
        )

    async def test_unexpected_error_hides_internal_details(self) -> None:
        with self.assertLogs("app.errors", level="ERROR") as logs:
            response = await self.client.get("/unexpected")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "internal_server_error",
                    "message": "An unexpected error occurred",
                }
            },
        )
        self.assertNotIn("sensitive internal detail", response.text)
        self.assertIn("Unhandled error", logs.output[0])


class OpenAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = app.openapi()

    def test_api_metadata_describes_service_and_tags(self) -> None:
        self.assertIn("pgvector", self.schema["info"]["description"])
        self.assertEqual(
            {tag["name"] for tag in self.schema["tags"]},
            {"system", "clients", "documents", "search"},
        )

    def test_operations_document_success_and_error_responses(self) -> None:
        operation = self.schema["paths"]["/clients"]["post"]

        self.assertEqual(operation["summary"], "Create a client")
        self.assertEqual(
            operation["responses"]["201"]["description"],
            "The newly created client",
        )
        self.assertEqual(
            operation["responses"]["409"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/ErrorResponse",
        )
        self.assertIn("422", operation["responses"])
        self.assertIn("500", operation["responses"])


if __name__ == "__main__":
    unittest.main()
