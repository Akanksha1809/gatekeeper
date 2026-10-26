-- Atomic token-bucket rate limiter.
--
-- KEYS[1]  bucket key, e.g. "ratelimit:{client_id}:{scope}"
-- ARGV[1]  capacity (max sustained tokens, integer)
-- ARGV[2]  refill_rate_per_sec (float)
-- ARGV[3]  burst (extra transient tokens, integer)
-- ARGV[4]  now_ms (integer, milliseconds, supplied by the caller so tests
--          can control time instead of depending on Redis's clock)
-- ARGV[5]  cost (tokens this request consumes, usually 1)
-- ARGV[6]  ttl_seconds (expiry applied to the bucket key so idle buckets
--          don't grow Redis memory unboundedly)
--
-- Returns {allowed (0/1), remaining_tokens (integer, floored), retry_after_ms}
--
-- The bucket state (tokens, last refill timestamp) is stored in a single
-- Redis hash so the whole read-modify-write happens atomically within this
-- script, which is itself atomic with respect to other Redis clients --
-- this is what makes it safe across multiple gateway replicas sharing the
-- same Redis instance.

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
local now_ms = tonumber(ARGV[4])
local cost = tonumber(ARGV[5])
local ttl_seconds = tonumber(ARGV[6])

local max_tokens = capacity + burst

local bucket = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(bucket[1])
local last_ts = tonumber(bucket[2])

if tokens == nil then
    tokens = max_tokens
    last_ts = now_ms
end

-- Refill based on elapsed time since last touch, capped at max_tokens.
local elapsed_ms = math.max(0, now_ms - last_ts)
local refilled = tokens + (elapsed_ms / 1000.0) * refill_rate
if refilled > max_tokens then
    refilled = max_tokens
end

local allowed = 0
local retry_after_ms = 0

if refilled >= cost then
    allowed = 1
    refilled = refilled - cost
else
    allowed = 0
    local deficit = cost - refilled
    if refill_rate > 0 then
        retry_after_ms = math.ceil((deficit / refill_rate) * 1000.0)
    else
        retry_after_ms = ttl_seconds * 1000
    end
end

redis.call("HSET", key, "tokens", refilled, "ts", now_ms)
redis.call("EXPIRE", key, ttl_seconds)

return {allowed, math.floor(refilled), retry_after_ms}
