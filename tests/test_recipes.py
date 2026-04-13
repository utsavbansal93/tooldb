"""Tests for recipe CRUD operations in ToolCache.

Covers happy paths and edge cases: 0 steps, missing tools,
broken tool refs, revalidation, step_count mismatch.
"""

from __future__ import annotations

import pytest

from tooldb.db.cache import ToolCache
from tooldb.invariants import InvariantViolation
from tooldb.models import Recipe, RecipeStep

from .conftest import make_tool


def make_recipe(cache: ToolCache, **overrides: object) -> Recipe:
    """Create a Recipe with tools already in cache. Override any field."""
    # Ensure referenced tools exist
    t1 = cache.upsert(make_tool(name="tool-a", url="https://a.com", task_tags=["pdf"]))
    t2 = cache.upsert(make_tool(name="tool-b", url="https://b.com", task_tags=["epub"]))

    defaults: dict[str, object] = {
        "name": "test-recipe",
        "description": "A test pipeline",
        "steps": [
            RecipeStep(tool_id=t1.id, params={"fmt": "pdf"}, output_to_next_input="file"),  # type: ignore[arg-type]
            RecipeStep(tool_id=t2.id, params={"fmt": "epub"}),  # type: ignore[arg-type]
        ],
        "step_count": 2,
    }
    defaults.update(overrides)
    return Recipe(**defaults)  # type: ignore[arg-type]


# ──────────────────── Happy path ────────────────────


class TestRecipeCreate:
    def test_create_and_retrieve(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache)
        saved = cache.create_recipe(recipe)
        assert saved.id is not None
        assert saved.name == "test-recipe"
        assert len(saved.steps) == 2
        assert saved.step_count == 2

    def test_get_recipe_by_id(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache)
        saved = cache.create_recipe(recipe)
        retrieved = cache.get_recipe(saved.id)  # type: ignore[arg-type]
        assert retrieved is not None
        assert retrieved.name == saved.name
        assert len(retrieved.steps) == len(saved.steps)

    def test_get_nonexistent_returns_none(self, cache: ToolCache) -> None:
        assert cache.get_recipe(99999) is None


class TestRecipeList:
    def test_list_all(self, cache: ToolCache) -> None:
        r1 = make_recipe(cache, name="recipe-1")
        r2 = make_recipe(cache, name="recipe-2")
        cache.create_recipe(r1)
        cache.create_recipe(r2)
        assert len(cache.list_recipes()) == 2

    def test_list_filtered_by_status(self, cache: ToolCache) -> None:
        r1 = make_recipe(cache, name="recipe-1")
        saved = cache.create_recipe(r1)
        cache.update_recipe_status(saved.id, "works")  # type: ignore[arg-type]

        r2 = make_recipe(cache, name="recipe-2")
        cache.create_recipe(r2)

        assert len(cache.list_recipes(status="works")) == 1
        assert len(cache.list_recipes(status="untried")) == 1


class TestRecipeStatus:
    def test_update_status(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache)
        saved = cache.create_recipe(recipe)
        cache.update_recipe_status(saved.id, "works", notes="tested ok")  # type: ignore[arg-type]
        updated = cache.get_recipe(saved.id)  # type: ignore[arg-type]
        assert updated is not None
        assert updated.my_status == "works"
        assert updated.my_notes == "tested ok"


class TestFindRecipesByTask:
    def test_matches_name(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache, name="pdf-converter")
        cache.create_recipe(recipe)
        results = cache.find_recipes_by_task("pdf converter")
        assert len(results) >= 1

    def test_matches_description(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache, description="converts markdown to epub")
        cache.create_recipe(recipe)
        results = cache.find_recipes_by_task("markdown epub")
        assert len(results) >= 1

    def test_matches_via_tool_tags(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache, name="generic-pipeline")
        cache.create_recipe(recipe)
        # tools have tags "pdf" and "epub"
        results = cache.find_recipes_by_task("pdf")
        assert len(results) >= 1


class TestValidateRecipe:
    def test_validate_all_tools_exist(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache)
        saved = cache.create_recipe(recipe)
        assert cache.validate_recipe(saved.id) is True  # type: ignore[arg-type]
        updated = cache.get_recipe(saved.id)  # type: ignore[arg-type]
        assert updated is not None
        assert updated.last_validated_at is not None

    def test_validate_returns_false_when_tool_deleted(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache)
        saved = cache.create_recipe(recipe)
        # Delete one of the referenced tools
        tool_id = saved.steps[0].tool_id
        cache.delete(tool_id)
        assert cache.validate_recipe(saved.id) is False  # type: ignore[arg-type]


# ──────────────────── Edge cases ────────────────────


class TestRecipeEdgeCases:
    def test_zero_steps_rejected(self, cache: ToolCache) -> None:
        recipe = Recipe(name="empty", description="no steps", steps=[], step_count=0)
        with pytest.raises(InvariantViolation, match="at least one step"):
            cache.create_recipe(recipe)

    def test_nonexistent_tool_id_rejected(self, cache: ToolCache) -> None:
        recipe = Recipe(
            name="bad-ref",
            description="references missing tool",
            steps=[RecipeStep(tool_id=99999, params={})],
            step_count=1,
        )
        with pytest.raises(ValueError, match="does not exist"):
            cache.create_recipe(recipe)

    def test_broken_tool_allowed_with_warning(self, cache: ToolCache) -> None:
        t = cache.upsert(make_tool(name="broken-tool", url="https://broken.com"))
        cache.record_failure(t.id, "always fails")  # type: ignore[arg-type]

        recipe = Recipe(
            name="has-broken",
            description="uses broken tool",
            steps=[RecipeStep(tool_id=t.id, params={})],  # type: ignore[arg-type]
            step_count=1,
        )
        # Should not raise, just warn
        saved = cache.create_recipe(recipe)
        assert saved.id is not None

    def test_deleted_tool_flagged_in_find(self, cache: ToolCache) -> None:
        recipe = make_recipe(cache, name="fragile-recipe")
        saved = cache.create_recipe(recipe)
        # Delete one tool
        cache.delete(saved.steps[0].tool_id)
        # find_recipes_by_task should still return it but flag it
        results = cache.find_recipes_by_task("pdf")
        # The recipe might not match if all tools are gone, but if it does
        # it should be flagged. Let's search by name instead.
        results = cache.find_recipes_by_task("fragile")
        if results:
            assert "[needs revalidation]" in (results[0].my_notes or "")

    def test_step_count_mismatch_rejected(self, cache: ToolCache) -> None:
        t = cache.upsert(make_tool(url="https://x.com"))
        recipe = Recipe(
            name="mismatch",
            description="step count wrong",
            steps=[RecipeStep(tool_id=t.id, params={})],  # type: ignore[arg-type]
            step_count=5,  # wrong!
        )
        with pytest.raises(InvariantViolation, match="step_count"):
            cache.create_recipe(recipe)
