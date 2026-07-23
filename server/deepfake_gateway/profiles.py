"""Central quality policies for the FaceFusion gateway."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QualityProfile:
    profile_id: str
    processors: tuple[str, ...]
    face_mask_types: tuple[str, ...]
    face_swapper_weight: float
    face_swapper_pixel_boost: str
    face_detector_model: str
    face_detector_size: str
    face_landmarker_model: str
    max_width: int
    face_enhancer_model: str | None = None
    face_enhancer_blend: int = 0
    face_enhancer_weight: float = 0.5

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.profile_id,
            "processors": list(self.processors),
            "face_mask_types": list(self.face_mask_types),
            "face_swapper_weight": self.face_swapper_weight,
            "face_swapper_pixel_boost": self.face_swapper_pixel_boost,
            "face_detector_model": self.face_detector_model,
            "face_detector_size": self.face_detector_size,
            "face_landmarker_model": self.face_landmarker_model,
            "max_width": self.max_width,
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
            face_swapper_pixel_boost="256x256",
            face_detector_model="scrfd",
            face_detector_size="320x320",
            face_landmarker_model="peppa_wutz",
            max_width=640,
        ),
        QualityProfile(
            profile_id="balanced",
            processors=("face_swapper",),
            face_mask_types=("box", "occlusion"),
            face_swapper_weight=0.65,
            face_swapper_pixel_boost="512x512",
            face_detector_model="yolo_face",
            face_detector_size="640x640",
            face_landmarker_model="2dfan4",
            max_width=960,
        ),
        QualityProfile(
            profile_id="quality",
            processors=("face_swapper",),
            face_mask_types=("box", "occlusion"),
            face_swapper_weight=0.65,
            face_swapper_pixel_boost="768x768",
            face_detector_model="yolo_face",
            face_detector_size="640x640",
            face_landmarker_model="2dfan4",
            max_width=1280,
        ),
    )
)
