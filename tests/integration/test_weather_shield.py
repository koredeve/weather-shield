from gltest import get_contract_factory


def test_deploy_smoke():
    factory = get_contract_factory("WeatherShield")
    contract = factory.deploy(args=[])
    result = contract.owner(args=[]).call()
    assert result is not None
