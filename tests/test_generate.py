from tests.support import balance, generate, new_user, set_scenario, unique


async def test_no_leg_fits_ref_images_returns_400_before_charge(api):
    user_id = await new_user(api, 10)
    response = await api.post(
        "/generate",
        json={
            "user_id": user_id,
            "model_slug": "image-fast",
            "prompt": "too many refs",
            "ref_images": [f"https://refs.example/{i}.png" for i in range(11)],
        },
    )
    assert response.status_code == 400
    assert (await balance(api, user_id))["balance"] == 10


async def test_insufficient_tokens_returns_402(api):
    user_id = await new_user(api, 10)
    response = await api.post(
        "/generate",
        json={"user_id": user_id, "model_slug": "video-long", "prompt": "expensive", "ref_images": []},
    )
    assert response.status_code == 402
    assert (await balance(api, user_id))["balance"] == 10


async def test_fourth_active_task_returns_429(api):
    user_id = await new_user(api, 20)
    prompt = unique("busy")
    await set_scenario("alpha", prompt, drop_rate=1, delay_min=0, delay_max=0)

    for _ in range(3):
        await generate(api, user_id, "image-fast", prompt)
    response = await api.post(
        "/generate",
        json={"user_id": user_id, "model_slug": "image-fast", "prompt": prompt, "ref_images": []},
    )

    assert response.status_code == 429
    assert (await balance(api, user_id))["balance"] == 14
