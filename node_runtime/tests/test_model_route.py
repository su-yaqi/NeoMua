from node_runtime.model_route import DirectRoute, ModelRouteStore, build_route_env


def test_gateway_route_builds_scoped_anthropic_environment(tmp_path) -> None:
    env = build_route_env(
        {
            "mode": "platform_gateway",
            "base_url": "https://gateway.example",
            "api_key": "scoped",
            "runtime_id": "runtime-1",
        },
        {"runtime_profile_id": "runtime-1", "model_id": "model-a"},
        ModelRouteStore(tmp_path / "routes.json"),
    )
    assert env["ANTHROPIC_API_KEY"] == "scoped"


def test_direct_route_requires_exact_verified_endpoint_and_model(tmp_path) -> None:
    store = ModelRouteStore(tmp_path / "routes.json")
    store.save(
        DirectRoute(
            runtime_id="runtime-1",
            base_url="https://anthropic.example",
            model_id="claude-a",
            api_key="secret",
            verified=True,
        )
    )
    env = build_route_env(
        {"mode": "direct_anthropic"},
        {
            "runtime_profile_id": "runtime-1",
            "base_url": "https://anthropic.example",
            "model_id": "claude-a",
        },
        store,
    )
    assert env["ANTHROPIC_BASE_URL"] == "https://anthropic.example"
    try:
        build_route_env(
            {"mode": "direct_anthropic"},
            {
                "runtime_profile_id": "runtime-1",
                "base_url": "https://anthropic.example",
                "model_id": "claude-b",
            },
            store,
        )
    except ValueError as exc:
        assert "verified" in str(exc)
    else:
        raise AssertionError("mismatched direct model was accepted")
