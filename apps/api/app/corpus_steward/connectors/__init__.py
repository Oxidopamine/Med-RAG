"""Versioned authoritative inventory connectors."""

from app.corpus_steward.connectors.base import Connector, HTTPConnectorTransport
from app.corpus_steward.connectors.synthetic import SyntheticConnector
from app.corpus_steward.connectors.who_smart import WHOSmartFHIRConnector


def connector_for(name: str, version: str) -> Connector:
    connector_types = {
        SyntheticConnector.name: SyntheticConnector,
        WHOSmartFHIRConnector.name: WHOSmartFHIRConnector,
    }
    try:
        connector = connector_types[name]()
    except KeyError as error:
        raise ValueError(f"unknown steward connector: {name}") from error
    if connector.version != version:
        raise ValueError(
            f"connector version mismatch for {name}: "
            f"registry={version}, runtime={connector.version}"
        )
    return connector


__all__ = [
    "Connector",
    "HTTPConnectorTransport",
    "SyntheticConnector",
    "WHOSmartFHIRConnector",
    "connector_for",
]
