# plan (previous team)
- at the start: post who owns what
  - config: the Redis cache fields, their validation, the schema files
  - client: the Redis client constructor and the cache wiring in grpc.go
  - tests: the config tests and the YAML fixtures
- as you go: push your branch, `git push team HEAD:refs/heads/<your-name>`, and post a status:
  what you changed, the test command you ran, its result, what you need from someone else
- before the hand-in: one of us merges the branches into team/main and runs the tests
