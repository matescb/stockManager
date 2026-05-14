from __future__ import annotations

from app.domain.sourcing.providers import factory


def test_single_client_per_provider(monkeypatch):
    factory._reset_provider_client_pool_for_tests()
    created = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.is_closed = False
            created.append(self)

        def close(self):
            self.is_closed = True

    monkeypatch.setattr(factory.httpx, "Client", FakeClient)
    try:
        with factory.make_retrying_client(provider_name="mouser", timeout=8.0) as mouser_1:
            pass
        with factory.make_retrying_client(provider_name="mouser", timeout=8.0) as mouser_2:
            pass
        with factory.make_retrying_client(provider_name="digikey", timeout=8.0) as digikey:
            pass

        assert mouser_1 is mouser_2
        assert mouser_1 is not digikey
        assert created == [mouser_1, digikey]
    finally:
        factory._reset_provider_client_pool_for_tests()
