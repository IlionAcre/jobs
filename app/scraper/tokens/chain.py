from __future__ import annotations

import logging
import time
from typing import Callable, Sequence

from app.scraper.config import FailurePolicyConfig
from app.scraper.errors import Challenged, MintChainExhausted, RateLimited, Transient
from app.scraper.models import Token
from app.scraper.tokens.minters import Minter

log = logging.getLogger("scraper.mint")


class MinterChain:
    """Try minters in order until one yields a token.

    The reaction depends on why a minter failed (research/upwork_recon/10):
      challenged    -> next minter at once; retrying a rejected fingerprint only adds bad traffic
      transient     -> retry the same minter with backoff, then move on
      rate limited  -> stop and re-raise; it is an IP limit, another minter would just add to it
    """

    def __init__(
        self,
        minters: Sequence[Minter],
        policy: FailurePolicyConfig,
        *,
        egress_id: str = "default",
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._minters = list(minters)
        self._policy = policy
        self._egress_id = egress_id
        self._sleep = sleep
        self._clock = clock

    def mint(self) -> Token:
        failures = []
        for minter in self._minters:
            for attempt in range(self._policy.transient_retries + 1):
                try:
                    value = minter.mint()
                except Challenged as ex:
                    log.warning("mint_challenged", extra={"minter": minter.name})
                    failures.append(f"{minter.name}: challenged ({ex})")
                    break
                except RateLimited:
                    log.warning("mint_rate_limited", extra={"minter": minter.name})
                    raise
                except Transient as ex:
                    failures.append(f"{minter.name}: {ex}")
                    if attempt >= self._policy.transient_retries:
                        log.warning("mint_transient_gave_up", extra={"minter": minter.name, "error": str(ex)[:200]})
                        break
                    backoff = self._policy.transient_backoff_s[min(attempt, len(self._policy.transient_backoff_s) - 1)]
                    log.info("mint_transient_retry", extra={"minter": minter.name, "attempt": attempt + 1, "wait_s": backoff})
                    self._sleep(backoff)
                else:
                    log.info("mint_ok", extra={"minter": minter.name, "fallback_depth": self._minters.index(minter)})
                    return Token(value=value, minted_at=self._clock(), minter=minter.name, egress_id=self._egress_id)
        log.error("mint_chain_exhausted", extra={"failures": failures[-10:]})
        raise MintChainExhausted("; ".join(failures[-10:]))
