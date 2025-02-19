# coding=utf-8
# Copyright 2023-present, the HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Contains tests for AsyncInferenceClient.

Tests are run directly with pytest instead of unittest.TestCase as it's much easier to run with asyncio.

Not all tasks are tested. We extensively test `text_generation` method since it's the most complex one (has different
return types + uses streaming requests on demand). Tests are mostly duplicates from test_inference_text_generation.py`.

For completeness we also run a test on a simple task (`test_async_sentence_similarity`) and assume all other tasks
work as well.
"""

import asyncio
import inspect

import pytest
from aiohttp import ClientResponseError

import huggingface_hub.inference._common
from huggingface_hub import AsyncInferenceClient, InferenceClient, InferenceTimeoutError
from huggingface_hub.inference._common import _is_tgi_server
from huggingface_hub.inference._text_generation import FinishReason, InputToken
from huggingface_hub.inference._text_generation import ValidationError as TextGenerationValidationError


@pytest.fixture(autouse=True)
def patch_non_tgi_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(huggingface_hub.inference._common, "_NON_TGI_SERVERS", set())


@pytest.fixture
def tgi_client() -> AsyncInferenceClient:
    return AsyncInferenceClient(model="google/flan-t5-xxl")


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_async_generate_no_details(tgi_client: AsyncInferenceClient) -> None:
    response = await tgi_client.text_generation("test", details=False, max_new_tokens=1)
    assert response == ""


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_async_generate_with_details(tgi_client: AsyncInferenceClient) -> None:
    response = await tgi_client.text_generation("test", details=True, max_new_tokens=1, decoder_input_details=True)

    assert response.generated_text == ""
    assert response.details.finish_reason == FinishReason.Length
    assert response.details.generated_tokens == 1
    assert response.details.seed is None
    assert len(response.details.prefill) == 1
    assert response.details.prefill[0] == InputToken(id=0, text="<pad>", logprob=None)
    assert len(response.details.tokens) == 1
    assert response.details.tokens[0].id == 3
    assert response.details.tokens[0].text == " "
    assert not response.details.tokens[0].special


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_async_generate_best_of(tgi_client: AsyncInferenceClient) -> None:
    response = await tgi_client.text_generation(
        "test", max_new_tokens=1, best_of=2, do_sample=True, decoder_input_details=True, details=True
    )

    assert response.details.seed is not None
    assert response.details.best_of_sequences is not None
    assert len(response.details.best_of_sequences) == 1
    assert response.details.best_of_sequences[0].seed is not None


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_async_generate_validation_error(tgi_client: AsyncInferenceClient) -> None:
    with pytest.raises(TextGenerationValidationError):
        await tgi_client.text_generation("test", max_new_tokens=10_000)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_async_generate_non_tgi_endpoint(tgi_client: AsyncInferenceClient) -> None:
    text = await tgi_client.text_generation("0 1 2", model="gpt2", max_new_tokens=10)
    assert text == " 3 4 5 6 7 8 9 10 11 12"
    assert not _is_tgi_server("gpt2")

    # Watermark is ignored (+ warning)
    with pytest.warns(UserWarning):
        await tgi_client.text_generation("4 5 6", model="gpt2", max_new_tokens=10, watermark=True)

    # Return as detail even if details=True (+ warning)
    with pytest.warns(UserWarning):
        text = await tgi_client.text_generation("0 1 2", model="gpt2", max_new_tokens=10, details=True)
    assert isinstance(text, str)

    # Return as stream raises error
    with pytest.raises(ValueError):
        await tgi_client.text_generation("0 1 2", model="gpt2", max_new_tokens=10, stream=True)


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_async_generate_stream_no_details(tgi_client: AsyncInferenceClient) -> None:
    responses = [
        response async for response in await tgi_client.text_generation("test", max_new_tokens=1, stream=True)
    ]

    assert len(responses) == 1
    response = responses[0]

    assert isinstance(response, str)
    assert response == " "


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_async_generate_stream_with_details(tgi_client: AsyncInferenceClient) -> None:
    responses = [
        response
        async for response in await tgi_client.text_generation("test", max_new_tokens=1, stream=True, details=True)
    ]

    assert len(responses) == 1
    response = responses[0]

    assert response.generated_text == ""
    assert response.details.finish_reason == FinishReason.Length
    assert response.details.generated_tokens == 1
    assert response.details.seed is None


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_async_sentence_similarity() -> None:
    async_client = AsyncInferenceClient()
    scores = await async_client.sentence_similarity(
        "Machine learning is so easy.",
        other_sentences=[
            "Deep learning is so straightforward.",
            "This is so difficult, like rocket science.",
            "I can't believe how much I struggled with this.",
        ],
    )
    assert scores == [0.7785726189613342, 0.4587625563144684, 0.2906219959259033]


def test_sync_vs_async_signatures() -> None:
    client = InferenceClient()
    async_client = AsyncInferenceClient()

    # Some methods have to be tested separately.
    special_methods = ["post", "text_generation"]

    # Post: this is not automatically tested. No need to test its signature separately.

    # Text-generation: return type changes from Iterable[...] to AsyncIterable[...] but input parameters are the same
    sync_method = getattr(client, "text_generation")
    assert not inspect.iscoroutinefunction(sync_method)
    async_method = getattr(async_client, "text_generation")
    assert inspect.iscoroutinefunction(async_method)

    sync_sig = inspect.signature(sync_method)
    async_sig = inspect.signature(async_method)
    assert sync_sig.parameters == async_sig.parameters
    assert sync_sig.return_annotation != async_sig.return_annotation

    [name for name in dir(client) if (not name.startswith("_")) and inspect.ismethod(getattr(client, name))]

    # Check that all methods are consistent between InferenceClient and AsyncInferenceClient
    for name in dir(client):
        if not inspect.ismethod(getattr(client, name)):  # not a method
            continue
        if name.startswith("_"):  # not public method
            continue
        if name in special_methods:  # tested separately
            continue

        # Check that the sync method is not async
        sync_method = getattr(client, name)
        assert not inspect.iscoroutinefunction(sync_method)

        # Check that the async method is async
        async_method = getattr(async_client, name)
        assert inspect.iscoroutinefunction(async_method)

        # Check that expected inputs and outputs are the same
        sync_sig = inspect.signature(sync_method)
        async_sig = inspect.signature(async_method)
        assert sync_sig.parameters == async_sig.parameters
        assert sync_sig.return_annotation == async_sig.return_annotation


@pytest.mark.asyncio
async def test_get_status_too_big_model() -> None:
    model_status = await AsyncInferenceClient().get_model_status("facebook/nllb-moe-54b")
    assert model_status.loaded is False
    assert model_status.state == "TooBig"
    assert model_status.compute_type == "cpu"
    assert model_status.framework == "transformers"


@pytest.mark.asyncio
async def test_get_status_loaded_model() -> None:
    model_status = await AsyncInferenceClient().get_model_status("bigscience/bloom")
    assert model_status.loaded is True
    assert model_status.state == "Loaded"
    assert isinstance(model_status.compute_type, dict)  # e.g. {'gpu': {'gpu': 'a100', 'count': 8}}
    assert model_status.framework == "text-generation-inference"


@pytest.mark.asyncio
async def test_get_status_unknown_model() -> None:
    with pytest.raises(ClientResponseError):
        await AsyncInferenceClient().get_model_status("unknown/model")


@pytest.mark.asyncio
async def test_get_status_model_as_url() -> None:
    with pytest.raises(NotImplementedError):
        await AsyncInferenceClient().get_model_status("https://unkown/model")


@pytest.mark.asyncio
async def test_list_deployed_models_single_frameworks() -> None:
    models_by_task = await AsyncInferenceClient().list_deployed_models("text-generation-inference")
    assert isinstance(models_by_task, dict)
    for task, models in models_by_task.items():
        assert isinstance(task, str)
        assert isinstance(models, list)
        for model in models:
            assert isinstance(model, str)

    assert "text-generation" in models_by_task
    assert "bigscience/bloom" in models_by_task["text-generation"]


@pytest.mark.asyncio
async def test_async_generate_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_aiohttp_client_timeout(*args, **kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr("aiohttp.ClientSession.post", _mock_aiohttp_client_timeout)
    with pytest.raises(InferenceTimeoutError):
        await AsyncInferenceClient(timeout=1).text_generation("test")


@pytest.mark.vcr
@pytest.mark.asyncio
async def test_unprocessable_entity_error() -> None:
    with pytest.raises(ClientResponseError) as error:
        await AsyncInferenceClient().conversational("Hi, who are you?", model="HuggingFaceH4/zephyr-7b-alpha")
    assert "Make sure 'conversational' task is supported by the model." in error.value.message
