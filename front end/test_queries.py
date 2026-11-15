import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from frontend_lambdas import ObtenerDashboard, ObtenerMaquinaDetalle, ObtenerMaquinas


def event(**fields):
    return {
        "requestContext": {"authorizer": {"jwt": {"claims": {
            "sub": "demo-user", "custom:tenant_id": "MINA_A",
        }}}},
        **fields,
    }


class DashboardQueries(unittest.TestCase):
    def test_requires_authorizer_claims(self):
        self.assertEqual(ObtenerMaquinas.lambda_handler({}, None)["statusCode"], 401)
        request = event()
        del request["requestContext"]["authorizer"]["jwt"]["claims"]["custom:tenant_id"]
        self.assertEqual(ObtenerMaquinas.lambda_handler(request, None)["statusCode"], 403)

    @patch("frontend_lambdas.queries.table")
    def test_pagination_uses_authorized_tenant(self, table):
        table.return_value.query.return_value = {
            "Items": [{"temperature": Decimal("91.5")}],
            "LastEvaluatedKey": {"tenant_id": "MINA_A", "machine_id": "M2"},
        }
        result = ObtenerMaquinas.lambda_handler(
            event(queryStringParameters={"tenant_id": "MINA_B", "limit": "1", "cursor": "M1"}), None
        )
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"]), {"items": [{"temperature": 91.5}], "next_cursor": "M2"})
        query = table.return_value.query.call_args.kwargs
        self.assertEqual(query["ExpressionAttributeValues"], {":tenant": "MINA_A"})
        self.assertEqual(query["ExclusiveStartKey"], {"tenant_id": "MINA_A", "machine_id": "M1"})

    @patch("frontend_lambdas.queries.table")
    def test_detail_scoped_to_tenant(self, table):
        table.return_value.get_item.return_value = {}
        result = ObtenerMaquinaDetalle.lambda_handler(event(pathParameters={"machine_id": "M9"}), None)
        self.assertEqual(result["statusCode"], 404)
        table.return_value.get_item.assert_called_once_with(Key={"tenant_id": "MINA_A", "machine_id": "M9"})

    @patch.dict(os.environ, {}, clear=True)
    def test_dashboard_identifies_missing_data_sources(self):
        result = ObtenerDashboard.lambda_handler(event(), None)
        self.assertEqual(result["statusCode"], 200)
        sections = json.loads(result["body"])["sections"]
        self.assertEqual(set(sections), {"machines", "incidents", "utilization", "efficacy", "predictions"})
        self.assertTrue(all(section == {"status": "not_configured"} for section in sections.values()))

    def test_invalid_limit(self):
        result = ObtenerMaquinas.lambda_handler(event(queryStringParameters={"limit": "1000"}), None)
        self.assertEqual(result["statusCode"], 400)


if __name__ == "__main__":
    unittest.main()
