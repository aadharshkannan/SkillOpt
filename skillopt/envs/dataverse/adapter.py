"""Dataverse environment adapter for SkillOpt."""
from __future__ import annotations

from skillopt.datasets.base import BatchSpec
from skillopt.envs.base import EnvAdapter
from skillopt.envs.dataverse.dataloader import DataverseSkillDataLoader


class DataverseSkillAdapter(EnvAdapter):
    """Dataverse environment adapter."""

    def __init__(
        self,
        skill_name: str,
        plugin_src_dir: str,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "split_dir",
        split_seed: int = 42,
        split_output_dir: str = "",
        max_turns: int = 1,
        exec_timeout: int = 180,
        workers: int = 24,
        live_workers: int = 4,
        analyst_workers: int = 16,
        failure_only: bool = False,
        minibatch_size: int = 8,
        edit_budget: int = 4,
        seed: int = 42,
        limit: int = 0,
        max_completion_tokens: int = 16384,
        live_enabled: bool = True,
        live_env_url: str = "",
        live_solution: str = "SkillOptEvals",
        live_prefix: str = "sko",
        record_bodies: bool = False,
    ) -> None:
        self.skill_name = skill_name
        self.plugin_src_dir = plugin_src_dir
        self.max_turns = max_turns
        self.exec_timeout = exec_timeout
        self.workers = workers
        self.live_workers = live_workers
        self.max_completion_tokens = int(max_completion_tokens)
        self.analyst_workers = analyst_workers
        self.failure_only = failure_only
        self.minibatch_size = minibatch_size
        self.edit_budget = edit_budget
        self.live_enabled = live_enabled
        self.live_env_url = live_env_url
        self.live_solution = live_solution
        self.live_prefix = live_prefix
        self.record_bodies = record_bodies
        self.dataloader = DataverseSkillDataLoader(
            split_dir=split_dir,
            data_path=data_path,
            split_mode=split_mode,
            split_seed=split_seed,
            split_output_dir=split_output_dir,
            seed=seed,
            limit=limit,
        )

    def setup(self, cfg: dict) -> None:
        super().setup(cfg)
        self.dataloader.setup(cfg)

    def get_dataloader(self):
        return self.dataloader

    def build_env_from_batch(self, batch: BatchSpec, **kwargs):
        return list(batch.payload or [])

    def build_train_env(self, batch_size: int, seed: int, **kwargs):
        batch = self.dataloader.build_train_batch(batch_size=batch_size, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs):
        batch = self.dataloader.build_eval_batch(env_num=env_num, split=split, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    def rollout(
        self,
        env_manager,
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict]:
        raise NotImplementedError("Task 17 wires this to dataverse.rollout.run_batch")

    def reflect(
        self,
        results: list[dict],
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict | None]:
        raise NotImplementedError("Task 22 wires this to run_minibatch_reflect with the patch-scope guardrail")


    def get_task_types(self) -> list[str]:
        return ["dataverse"]
