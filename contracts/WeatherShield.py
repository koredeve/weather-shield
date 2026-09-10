# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json
import time


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"

STATUS_ACTIVE = "active"
STATUS_PAID = "paid"
STATUS_DENIED = "denied"

PERIL_RAIN = "rain_mm"
PERIL_WIND = "wind_kmh"
PERIL_SNOW = "snow_cm"
PERIL_TEMP_LOW = "temp_low_c"

UPWARD_PERILS = (PERIL_RAIN, PERIL_WIND, PERIL_SNOW)

VALUE_TOLERANCE_X10 = 2


def _handle_leader_error(leaders_res, leader_fn) -> bool:
	leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
	try:
		leader_fn()
		return False
	except gl.vm.UserError as e:
		validator_msg = e.message if hasattr(e, "message") else str(e)
		if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
			return validator_msg == leader_msg
		if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
			return True
		return False
	except Exception:
		return False


@gl.evm.contract_interface
class _Recipient:
	class View:
		pass

	class Write:
		pass


@allow_storage
@dataclass
class Policy:
	holder: Address
	location: str
	peril: str
	threshold_x10: u256
	coverage_atto: u256
	premium_atto: u256
	status: str
	last_value_x10: i64
	endpoint: str
	check_after: u256
	check_before: u256


class WeatherShield(gl.Contract):
	owner_addr: Address
	base_url: str
	insurance_pool: u256
	reserve_fund: u256
	exposure: u256
	policies: TreeMap[str, Policy]
	credits: TreeMap[Address, u256]
	policy_ids: DynArray[str]

	def __init__(self) -> None:
		self.owner_addr = gl.message.sender_address
		self.base_url = ""
		self.insurance_pool = u256(0)
		self.reserve_fund = u256(0)
		self.exposure = u256(0)

	def _get_policy(self, policy_id: str) -> Policy:
		policy = self.policies.get(policy_id)
		if policy is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unknown policy id")
		return policy

	@gl.public.view
	def owner(self) -> Address:
		return self.owner_addr

	@gl.public.view
	def get_base_url(self) -> str:
		return self.base_url

	@gl.public.write
	def set_base_url(self, url: str) -> None:
		if gl.message.sender_address != self.owner_addr:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only owner")
		clean_url = str(url).strip()
		if not clean_url.startswith("https://"):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} URL must start with https://")
		self.base_url = clean_url

	@gl.public.write.payable
	def fund_reserve(self) -> None:
		if gl.message.value == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Send value with the call")
		self.reserve_fund = self.reserve_fund + gl.message.value

	@gl.public.view
	def get_reserves(self) -> dict:
		return {
			"insurance_pool": self.insurance_pool,
			"reserve_fund": self.reserve_fund,
			"exposure": self.exposure,
		}

	@gl.public.write.payable
	def buy_policy(
		self,
		policy_id: str,
		location: str,
		peril: str,
		threshold_x10: u256,
		coverage_atto: u256,
		check_after_timestamp: u256 = u256(0),
		check_before_timestamp: u256 = u256(0),
	) -> None:
		if peril not in UPWARD_PERILS and peril != PERIL_TEMP_LOW:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unsupported peril")
		if u256(coverage_atto) == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Coverage must be greater than zero")
		clean_id = str(policy_id).strip()
		clean_loc = str(location).strip()
		if not clean_id or not clean_loc:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Policy id and location must not be empty")
		expected_premium = u256(int(coverage_atto) // 10)
		if gl.message.value != expected_premium:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Premium mismatch, send coverage/10")
		url_base = str(self.base_url).strip()
		if not url_base:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Base URL not configured")
		if clean_id in self.policies:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Policy id already exists")

		# Solvency Invariant: total exposure cannot exceed backed funds (pool + reserve)
		if self.insurance_pool + self.reserve_fund < self.exposure + coverage_atto:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Insufficient insurer reserve to guarantee coverage solvency"
			)

		self.insurance_pool = self.insurance_pool + expected_premium
		self.exposure = self.exposure + coverage_atto

		check_after = check_after_timestamp
		if check_after == u256(0):
			check_after = u256(int(time.time()))

		self.policies[clean_id] = Policy(
			holder=gl.message.sender_address,
			location=clean_loc,
			peril=str(peril),
			threshold_x10=u256(threshold_x10),
			coverage_atto=u256(coverage_atto),
			premium_atto=u256(gl.message.value),
			status=STATUS_ACTIVE,
			last_value_x10=i64(0),
			endpoint=url_base,
			check_after=check_after,
			check_before=check_before_timestamp,
		)
		self.policy_ids.append(clean_id)

	@gl.public.write
	def check_weather(self, policy_id: str) -> None:
		policy = self._get_policy(policy_id)
		if policy.status != STATUS_ACTIVE:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Policy is not active")

		now = u256(int(time.time()))
		if now < policy.check_after:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Premature check: coverage window has not started yet"
			)
		if policy.check_before != u256(0) and now > policy.check_before:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Check window has expired")

		# Invariant: Immutable endpoint permanently bound at purchase time
		url_base = str(policy.endpoint)
		location = str(policy.location)
		peril = str(policy.peril)
		threshold = int(policy.threshold_x10)
		is_upward = peril in UPWARD_PERILS

		def leader_fn() -> dict:
			res = gl.nondet.web.get(url_base + "?location=" + location + "&peril=" + peril)
			if res.status >= 500:
				raise gl.vm.UserError(
					f"{ERROR_TRANSIENT} Weather API returned status {res.status}"
				)
			if res.status >= 400:
				raise gl.vm.UserError(f"{ERROR_EXTERNAL} Weather API returned status {res.status}")
			try:
				payload = json.loads(bytes(res.body).decode("utf-8"))
			except Exception:
				raise gl.vm.UserError(f"{ERROR_EXTERNAL} malformed weather payload")
			if not isinstance(payload, dict) or "value" not in payload:
				raise gl.vm.UserError(f"{ERROR_EXTERNAL} missing weather value in payload")
			try:
				value = float(payload["value"])
			except Exception:
				raise gl.vm.UserError(f"{ERROR_EXTERNAL} non-numeric weather value")
			value_x10 = int(round(value * 10))
			if is_upward:
				triggered = value_x10 >= threshold
			else:
				triggered = value_x10 <= threshold
			return {
				"triggered": bool(triggered),
				"value_x10": int(value_x10),
				"location": location,
				"peril": peril,
				"status": "FINAL",
			}

		def validator_fn(leaders_res: gl.vm.Result) -> bool:
			if not isinstance(leaders_res, gl.vm.Return):
				return _handle_leader_error(leaders_res, leader_fn)
			leader_data = leaders_res.calldata
			if not isinstance(leader_data, dict):
				return False
			# Invariant: Complete typed schema validation
			for req_field in ("triggered", "value_x10", "location", "peril", "status"):
				if req_field not in leader_data:
					return False
			if not isinstance(leader_data["triggered"], bool):
				return False
			if not isinstance(leader_data["value_x10"], int):
				return False
			if not isinstance(leader_data["location"], str) or not leader_data["location"]:
				return False
			if not isinstance(leader_data["peril"], str) or not leader_data["peril"]:
				return False
			if leader_data["status"] != "FINAL":
				return False

			fresh = leader_fn()
			leader_triggered = bool(leader_data.get("triggered", False))
			fresh_triggered = bool(fresh.get("triggered", False))
			if leader_triggered != fresh_triggered:
				return False
			leader_x10 = int(leader_data.get("value_x10", 0))
			fresh_x10 = int(fresh.get("value_x10", 0))
			return abs(leader_x10 - fresh_x10) <= VALUE_TOLERANCE_X10

		result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

		policy.last_value_x10 = i64(int(result["value_x10"]))
		self.exposure = self.exposure - policy.coverage_atto
		if bool(result["triggered"]):
			policy.status = STATUS_PAID
			holder = policy.holder
			if self.insurance_pool >= policy.coverage_atto:
				self.insurance_pool = self.insurance_pool - policy.coverage_atto
			else:
				remainder = policy.coverage_atto - self.insurance_pool
				self.insurance_pool = u256(0)
				self.reserve_fund = self.reserve_fund - remainder
			self.credits[holder] = self.credits.get(holder, u256(0)) + policy.coverage_atto
		else:
			policy.status = STATUS_DENIED

	@gl.public.write
	def withdraw(self) -> None:
		who = gl.message.sender_address
		amount = self.credits.get(who, u256(0))
		if amount == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Nothing to withdraw")
		self.credits[who] = u256(0)
		_Recipient(who).emit_transfer(value=u256(amount))

	@gl.public.view
	def get_policy(self, policy_id: str) -> dict:
		policy = self._get_policy(policy_id)
		return {
			"holder": str(policy.holder),
			"location": policy.location,
			"peril": policy.peril,
			"threshold_x10": policy.threshold_x10,
			"coverage_atto": policy.coverage_atto,
			"premium_atto": policy.premium_atto,
			"status": policy.status,
			"last_value_x10": policy.last_value_x10,
			"endpoint": policy.endpoint,
			"check_after": policy.check_after,
			"check_before": policy.check_before,
		}

	@gl.public.view
	def credit_of(self, who: Address) -> u256:
		return self.credits.get(Address(who), u256(0))

	@gl.public.view
	def total_policies(self) -> u256:
		return u256(len(self.policy_ids))
