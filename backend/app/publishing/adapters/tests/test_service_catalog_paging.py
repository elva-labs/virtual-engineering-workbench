"""Service Catalog lists of a portfolio are read to their last page."""

from unittest.mock import MagicMock

import assertpy

from app.publishing.adapters.query_services import service_catalog_query_service
from app.publishing.adapters.services import service_catalog_service


def _principal(i: int) -> dict:
    return {"PrincipalARN": f"arn:aws:iam::123456789012:role/role-{i:03d}", "PrincipalType": "IAM"}


def test_list_roles_for_portfolio_keeps_every_page(mock_moto_calls):
    # Each page used to replace the ones before it.
    mock_moto_calls["ListPrincipalsForPortfolio"] = MagicMock(
        side_effect=[
            {"Principals": [_principal(i) for i in range(0, 100)], "NextPageToken": "p2"},
            {"Principals": [_principal(i) for i in range(100, 200)], "NextPageToken": "p3"},
            {"Principals": [_principal(i) for i in range(200, 300)]},
        ]
    )
    sc_srv = service_catalog_service.ServiceCatalogService(
        "admin", "usecase", "launchconstraint", lambda region: f"arn-{region}", "ALLOWED", "123456789012", "bucket"
    )

    roles = sc_srv.list_roles_for_portfolio("us-east-1", "sc-port-00000")

    assertpy.assert_that(roles).is_length(300)
    assertpy.assert_that(roles[0]).is_equal_to("role-000")
    assertpy.assert_that(roles[-1]).is_equal_to("role-299")


def test_get_launch_constraint_id_reads_past_the_first_page(mock_moto_calls, mock_logger):
    mock_moto_calls["ListConstraintsForPortfolio"] = MagicMock(
        side_effect=[
            {"ConstraintDetails": [{"ConstraintId": "cons-n", "Type": "NOTIFICATION"}], "NextPageToken": "p2"},
            {"ConstraintDetails": [{"ConstraintId": "cons-l", "Type": "LAUNCH"}]},
        ]
    )
    sc_qry_srv = service_catalog_query_service.ServiceCatalogQueryService(
        "admin", "usecase", ["OwnerTID"], "123456789012", mock_logger
    )

    assertpy.assert_that(sc_qry_srv.get_launch_constraint_id("us-east-1", "port-1", "prod-1")).is_equal_to("cons-l")
    assertpy.assert_that(mock_moto_calls["ListConstraintsForPortfolio"].call_count).is_equal_to(2)
