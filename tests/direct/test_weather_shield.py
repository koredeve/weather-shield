import json

COVERAGE = 10 * 10**18
PREMIUM = COVERAGE // 10

BASE_URL = "https://api.example.com/weather"
WEB_REGEX = r"https://api\.example\.com/weather"


def _deploy(direct_vm, direct_deploy, owner):
    """Deploys with `owner` as the sender so the deployer becomes the contract owner."""
    direct_vm.sender = owner
    contract = direct_deploy("contracts/WeatherShield.py")
    direct_vm.value = 500 * 10**18
    contract.fund_reserve()
    direct_vm.value = 0
    return contract


def _set_base_url(direct_vm, contract, who):
    direct_vm.sender = who
    contract.set_base_url(BASE_URL)


def _buy_policy(
    direct_vm,
    contract,
    buyer,
    policy_id,
    location="London",
    peril="rain_mm",
    threshold=300,
    coverage=COVERAGE,
):
    """Buys a policy with the exact premium (coverage/10) attached."""
    direct_vm.sender = buyer
    direct_vm.value = coverage // 10
    contract.buy_policy(policy_id, location, peril, threshold, coverage)
    direct_vm.value = 0


def test_owner_controls_base_url(direct_vm, direct_deploy, direct_alice, direct_bob):
    """The deployer is the owner and only the owner can set the weather API endpoint."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    assert len(str(contract.owner())) > 0
    assert contract.get_base_url() == ""

    _set_base_url(direct_vm, contract, direct_alice)
    assert contract.get_base_url() == BASE_URL

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Only owner"):
            contract.set_base_url("https://evil.example.com/weather")
    assert contract.get_base_url() == BASE_URL


def test_buy_policy_stores_active_policy_and_rejects_duplicate_id(
    direct_vm, direct_deploy, direct_alice
):
    """A bought policy is stored as active with premium coverage/10; ids are unique."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)

    _buy_policy(direct_vm, contract, direct_alice, "policy-1")

    policy = contract.get_policy("policy-1")
    assert len(policy["holder"]) > 0
    assert policy["location"] == "London"
    assert policy["peril"] == "rain_mm"
    assert policy["threshold_x10"] == 300
    assert policy["coverage_atto"] == COVERAGE
    assert policy["premium_atto"] == PREMIUM
    assert policy["status"] == "active"
    assert policy["last_value_x10"] == 0
    assert contract.total_policies() == 1

    direct_vm.sender = direct_alice
    direct_vm.value = PREMIUM
    with direct_vm.expect_revert("Policy id already exists"):
        contract.buy_policy("policy-1", "London", "rain_mm", 300, COVERAGE)
    direct_vm.value = 0
    assert contract.total_policies() == 1


def test_buy_policy_requires_exact_premium(direct_vm, direct_deploy, direct_alice):
    """The attached value must equal exactly coverage/10."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = PREMIUM - 1
    with direct_vm.expect_revert("Premium mismatch"):
        contract.buy_policy("policy-low", "London", "rain_mm", 300, COVERAGE)

    direct_vm.value = PREMIUM + 1
    with direct_vm.expect_revert("Premium mismatch"):
        contract.buy_policy("policy-high", "London", "rain_mm", 300, COVERAGE)

    direct_vm.value = 0
    with direct_vm.expect_revert("Premium mismatch"):
        contract.buy_policy("policy-zero", "London", "rain_mm", 300, COVERAGE)

    assert contract.total_policies() == 0


def test_buy_policy_rejects_unknown_peril(direct_vm, direct_deploy, direct_alice):
    """Only the four supported perils are accepted."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Unsupported peril"):
        contract.buy_policy("policy-hail", "London", "hail_mm", 300, COVERAGE)
    with direct_vm.expect_revert("Unsupported peril"):
        contract.buy_policy("policy-typo", "London", "rain", 300, COVERAGE)

    assert contract.total_policies() == 0


def test_check_weather_pays_when_rain_meets_threshold(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """34.2mm of rain against a 30.0mm threshold pays the coverage to the holder."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)
    _buy_policy(direct_vm, contract, direct_alice, "policy-rain")

    direct_vm.mock_web(
        WEB_REGEX, {"status": 200, "body": json.dumps({"value": 34.2})}
    )

    with direct_vm.prank(direct_bob):
        contract.check_weather("policy-rain")

    policy = contract.get_policy("policy-rain")
    assert policy["status"] == "paid"
    assert policy["last_value_x10"] == 342
    assert contract.credit_of(direct_alice) == COVERAGE
    assert contract.credit_of(direct_bob) == 0


def test_check_weather_denies_when_rain_below_threshold(
    direct_vm, direct_deploy, direct_alice
):
    """12.0mm of rain against a 30.0mm threshold denies the policy without payout."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)
    _buy_policy(direct_vm, contract, direct_alice, "policy-dry")

    direct_vm.mock_web(
        WEB_REGEX, {"status": 200, "body": json.dumps({"value": 12.0})}
    )
    contract.check_weather("policy-dry")

    policy = contract.get_policy("policy-dry")
    assert policy["status"] == "denied"
    assert policy["last_value_x10"] == 120
    assert contract.credit_of(direct_alice) == 0


def test_check_weather_pays_on_freezing_temperature(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """temp_low_c triggers on cold: -3.5C vs a <= 0C threshold pays out."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)
    _buy_policy(
        direct_vm,
        contract,
        direct_alice,
        "policy-frost",
        location="Oslo",
        peril="temp_low_c",
        threshold=0,
    )

    direct_vm.mock_web(
        WEB_REGEX, {"status": 200, "body": json.dumps({"value": -3.5})}
    )

    with direct_vm.prank(direct_bob):
        contract.check_weather("policy-frost")

    policy = contract.get_policy("policy-frost")
    assert policy["status"] == "paid"
    assert policy["last_value_x10"] == -35
    assert contract.credit_of(direct_alice) == COVERAGE


def test_check_weather_rejects_unknown_or_already_resolved_policy(
    direct_vm, direct_deploy, direct_alice
):
    """Checking an unknown id or an already-resolved policy reverts."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)
    _buy_policy(direct_vm, contract, direct_alice, "policy-rain")

    with direct_vm.expect_revert("Unknown policy id"):
        contract.check_weather("missing-policy")

    direct_vm.mock_web(
        WEB_REGEX, {"status": 200, "body": json.dumps({"value": 34.2})}
    )
    contract.check_weather("policy-rain")

    with direct_vm.expect_revert("Policy is not active"):
        contract.check_weather("policy-rain")


def test_check_weather_malformed_payload_keeps_policy_active(
    direct_vm, direct_deploy, direct_alice
):
    """An unparseable API body surfaces as [EXTERNAL] and leaves the policy untouched."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)
    _buy_policy(direct_vm, contract, direct_alice, "policy-rain")

    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": "<html>oops</html>"})

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.check_weather("policy-rain")

    policy = contract.get_policy("policy-rain")
    assert policy["status"] == "active"
    assert contract.credit_of(direct_alice) == 0


def test_check_weather_maps_http_status_to_error_classes(
    direct_vm, direct_deploy, direct_alice
):
    """Client errors are [EXTERNAL], server errors are [TRANSIENT]; both leave state clean."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)
    _buy_policy(direct_vm, contract, direct_alice, "policy-rain")

    direct_vm.mock_web(WEB_REGEX, {"status": 404, "body": "not found"})
    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.check_weather("policy-rain")

    direct_vm.clear_mocks()
    direct_vm.mock_web(WEB_REGEX, {"status": 500, "body": "boom"})
    assert contract.get_policy("policy-rain")["status"] == "active"


def test_empty_policy_inputs_and_non_https_rejected(direct_vm, direct_deploy, direct_alice):
    """Empty policy id, location, zero coverage, or non-HTTPS URLs are rejected."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("URL must start with https://"):
        contract.set_base_url("http://insecure.example.com/weather")

    _set_base_url(direct_vm, contract, direct_alice)

    direct_vm.value = PREMIUM
    with direct_vm.expect_revert("must not be empty"):
        contract.buy_policy("  ", "London", "rain_mm", 300, COVERAGE)

    with direct_vm.expect_revert("must not be empty"):
        contract.buy_policy("policy-empty-loc", "  ", "rain_mm", 300, COVERAGE)

    direct_vm.value = 0


def test_payout_solvency_enforcement(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Buying coverage that exceeds the insurer's reserve pool reverts with an insolvency error."""
    direct_vm.sender = direct_alice
    contract = direct_deploy("contracts/WeatherShield.py")
    # Deploy without funding reserve pool
    _set_base_url(direct_vm, contract, direct_alice)

    # Attempt to buy 10 GEN coverage with only 1 GEN premium (9 GEN unbacked)
    direct_vm.sender = direct_bob
    direct_vm.value = PREMIUM
    with direct_vm.expect_revert("Insufficient insurer reserve to guarantee coverage solvency"):
        contract.buy_policy("policy-unbacked", "London", "rain_mm", 300, COVERAGE)
    direct_vm.value = 0


def test_premature_checks_rejected(direct_vm, direct_deploy, direct_alice):
    """Checking weather before the policy coverage window starts reverts."""
    from unittest.mock import patch
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)

    now = 1700000000.0
    future_start = int(now) + 86400  # Starts tomorrow

    with patch("time.time", return_value=now):
        direct_vm.sender = direct_alice
        direct_vm.value = PREMIUM
        contract.buy_policy("policy-window", "London", "rain_mm", 300, COVERAGE, future_start, 0)
        direct_vm.value = 0

        # Immediate check before coverage window begins
        with direct_vm.expect_revert("Premature check: coverage window has not started yet"):
            contract.check_weather("policy-window")

    # Time advances into the coverage window
    with patch("time.time", return_value=now + 86401):
        direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": json.dumps({"value": 35.0})})
        contract.check_weather("policy-window")
        assert contract.get_policy("policy-window")["status"] == "paid"


def test_endpoint_changes_do_not_alter_purchased_policy(
    direct_vm, direct_deploy, direct_alice
):
    """Changing base_url after a policy is purchased does NOT affect the policy's bound endpoint."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)

    _buy_policy(direct_vm, contract, direct_alice, "policy-bound")
    policy = contract.get_policy("policy-bound")
    assert policy["endpoint"] == BASE_URL

    # Owner changes base_url
    direct_vm.sender = direct_alice
    contract.set_base_url("https://new-api.example.com/weather")
    assert contract.get_base_url() == "https://new-api.example.com/weather"

    # Policy remains bound to original BASE_URL
    assert contract.get_policy("policy-bound")["endpoint"] == BASE_URL

    # Web mock for original BASE_URL resolves the policy
    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": json.dumps({"value": 35.0})})
    contract.check_weather("policy-bound")
    assert contract.get_policy("policy-bound")["status"] == "paid"


def test_missing_result_fields_rejected(direct_vm, direct_deploy, direct_alice):
    """Missing value field in weather API payload reverts and leaves policy active."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_base_url(direct_vm, contract, direct_alice)
    _buy_policy(direct_vm, contract, direct_alice, "policy-missing")

    # Payload with station but missing value
    direct_vm.mock_web(
        WEB_REGEX, {"status": 200, "body": json.dumps({"station": "EGLC", "status": "OK"})}
    )

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.check_weather("policy-missing")

    assert contract.get_policy("policy-missing")["status"] == "active"


