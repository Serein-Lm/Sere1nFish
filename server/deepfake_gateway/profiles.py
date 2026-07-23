"""Central quality policies for the FaceFusion gateway."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QualityProfile:
    profile_id: str
    processors: tuple[str, ...]
    face_mask_types: tuple[str, ...]
    face_swapper_weight: float
    face_enhancer_model: str | None = None
    face_enhancer_blend: int = 0
    face_enhancer_weight: float = 0.5

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.profile_id,
            "processors": list(self.processors),
            "face_mask_types": list(self.face_mask_types),
            "face_swapper_weight": self.face_swapper_weight,
            "face_enhancer_model": self.face_enhancer_model,
            "face_enhancer_blend": self.face_enhancer_blend,
        }


class QualityProfileRegistry:
    def __init__(self, profiles: tuple[QualityProfile, ...]) -> None:
        self._profiles = {profile.profile_id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("Deepfake quality profile IDs must be unique")

    def get(self, profile_id: str) -> QualityProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            choices = ", ".join(self._profiles)
            raise ValueError(f"Unknown quality profile '{profile_id}'; choose one of: {choices}") from exc

    def all(self) -> tuple[QualityProfile, ...]:
        return tuple(self._profiles.values())

    def processor_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(name for profile in self._profiles.values() for name in profile.processors))


QUALITY_PROFILES = QualityProfileRegistry(
    (
        QualityProfile(
            profile_id="fast",
            processors=("face_swapper",),
            face_mask_types=("box",),
            face_swapper_weight=0.65,
        ),
        QualityProfile(
            profile_id="balanced",
            processors=("face_swapper",),
            face_mask_types=("box", "occlusion"),
            face_swapper_weight=0.65,
        ),
        QualityProfile(
            profile_id="quality",
            processors=("face_swapper", "face_enhancer"),
            face_mask_types=("box", "occlusion"),
            face_swapper_weight=0.65,
            face_enhancer_model="gfpgan_1.4",
            face_enhancer_blend=60,
            face_enhancer_weight=0.5,
        ),
    )
)
