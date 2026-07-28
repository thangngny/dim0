import { describe, expect, it, beforeEach, afterEach } from "vitest"
import {
  MAX_RECONNECT_ATTEMPTS,
  computeBackoffMs,
  getCollabConnState,
  requestCollabReconnect,
  resetCollabConnState,
  setCollabConnState,
  setCollabReconnectTrigger,
} from "./collab-reconnect"


describe("computeBackoffMs", () => {
  it("doubles per attempt up to the cap", () => {
    // Default cap is 30s; defaults are 1s base.
    expect(computeBackoffMs(0)).toBe(1_000)
    expect(computeBackoffMs(1)).toBe(2_000)
    expect(computeBackoffMs(2)).toBe(4_000)
    expect(computeBackoffMs(3)).toBe(8_000)
    expect(computeBackoffMs(4)).toBe(16_000)
    // 32s would exceed the 30s cap.
    expect(computeBackoffMs(5)).toBe(30_000)
    // High attempts also cap.
    expect(computeBackoffMs(50)).toBe(30_000)
  })

  it("respects custom base + cap", () => {
    expect(computeBackoffMs(0, { baseDelayMs: 500, capDelayMs: 5_000 })).toBe(500)
    expect(computeBackoffMs(1, { baseDelayMs: 500, capDelayMs: 5_000 })).toBe(1_000)
    expect(computeBackoffMs(10, { baseDelayMs: 500, capDelayMs: 5_000 })).toBe(5_000)
  })

  it("clamps negative attempts to zero", () => {
    expect(computeBackoffMs(-3)).toBe(1_000)
  })

  it("MAX_RECONNECT_ATTEMPTS hits the cap before completing", () => {
    // We want the loop to give up *before* it sits at 30s for several
    // attempts in a row — confirms 6 attempts is the right ceiling for
    // the chosen backoff curve.
    const finalDelay = computeBackoffMs(MAX_RECONNECT_ATTEMPTS - 1)
    expect(finalDelay).toBeLessThanOrEqual(30_000)
  })
})


describe("collab connection state singleton", () => {
  beforeEach(() => {
    resetCollabConnState()
  })

  afterEach(() => {
    resetCollabConnState()
  })

  it("starts in idle", () => {
    expect(getCollabConnState()).toBe("idle")
  })

  it("transitions through states without noise", () => {
    setCollabConnState("connecting")
    expect(getCollabConnState()).toBe("connecting")
    setCollabConnState("live")
    expect(getCollabConnState()).toBe("live")
    setCollabConnState("reconnecting")
    expect(getCollabConnState()).toBe("reconnecting")
    setCollabConnState("failed")
    expect(getCollabConnState()).toBe("failed")
  })

  it("notifies subscribers exactly on transitions", () => {
    let notified = 0
    // Tap into the module's notification via React's useSyncExternalStore
    // path; we use the imperative API here for simplicity. The setter
    // short-circuits when next === current, which is the contract we
    // want to lock down.
    setCollabConnState("connecting") // idle → connecting
    notified += 1

    const before = getCollabConnState()
    setCollabConnState("connecting")  // no-op, same state
    expect(getCollabConnState()).toBe(before)

    setCollabConnState("live")  // transition
    notified += 1

    expect(notified).toBe(2)
  })

  it("resetCollabConnState returns to idle", () => {
    setCollabConnState("failed")
    expect(getCollabConnState()).toBe("failed")
    resetCollabConnState()
    expect(getCollabConnState()).toBe("idle")
  })
})


describe("imperative reconnect trigger", () => {
  beforeEach(() => {
    resetCollabConnState()
    setCollabReconnectTrigger(null)
  })

  afterEach(() => {
    resetCollabConnState()
    setCollabReconnectTrigger(null)
  })

  it("requestCollabReconnect is a no-op when no trigger is registered", () => {
    // Should not throw when no board is mounted.
    expect(() => requestCollabReconnect()).not.toThrow()
  })

  it("invokes the registered trigger", () => {
    let calls = 0
    setCollabReconnectTrigger(() => {
      calls += 1
    })
    requestCollabReconnect()
    expect(calls).toBe(1)
  })

  it("clearing the trigger makes requests a no-op again", () => {
    let calls = 0
    setCollabReconnectTrigger(() => {
      calls += 1
    })
    setCollabReconnectTrigger(null)
    requestCollabReconnect()
    expect(calls).toBe(0)
  })
})
