import cv2
import numpy as np
import pytest

from app.imaging import detect_corners, perspective_crop, valid_corners, encode_cover, decode_image, Orientation, process_cover


def test_skewed_rectangle_and_invalid_crossed_corners():
    frame = np.full((800, 1000, 3), 35, np.uint8)
    points = np.array([[280, 100], [700, 150], [780, 700], [190, 650]], np.int32)
    cv2.fillConvexPoly(frame, points, (235, 220, 200))
    corners, found = detect_corners(frame)
    assert found
    assert np.linalg.norm(corners - points, axis=1).max() < 15
    output = perspective_crop(frame, corners)
    assert output.shape[0] > 500
    assert not valid_corners(corners[[0, 2, 1, 3]])
    with pytest.raises(ValueError):
        perspective_crop(frame, corners[[0, 2, 1, 3]])


def test_uncertain_preserves_frame_and_encoded_limit():
    frame = np.full((1800, 1400, 3), 90, np.uint8)
    points, found = detect_corners(frame)
    assert not found
    assert points[0].tolist() == [0, 0]
    assert points[2].tolist() == [1399, 1799]
    encoded = encode_cover(frame)
    assert max(decode_image(encoded).shape[:2]) == 1280


def orientation_fixture():
    frame = np.full((640, 420, 3), 245, np.uint8)
    for i, text in enumerate(("COMPUTER SYSTEMS", "Understanding a book", "Reading and learning", "A guide to computing", "Practical information", "2026 THIRD EDITION")):
        cv2.putText(frame, text, (20, 80+75*i), cv2.FONT_HERSHEY_SIMPLEX, .6, (20, 30, 40), 2)
    return frame


def test_local_model_four_rotations_and_compensation():
    frame = orientation_fixture()
    for clockwise in range(4):
        rotated = np.ascontiguousarray(np.rot90(frame, -clockwise))
        angle, probability = Orientation.predict(rotated)
        assert angle == clockwise * 90, (clockwise, angle, probability)
        result = process_cover(rotated)
        assert result["rotation"] == clockwise
        assert result["image"].shape[0] > result["image"].shape[1]


def test_manual_rotation_does_not_trigger_orientation_again(monkeypatch):
    def forbidden(*args):
        raise AssertionError("manual rotation must not rerun model")
    monkeypatch.setattr(Orientation, "predict", forbidden)
    result = process_cover(orientation_fixture(), auto_orientation=False, rotation=1)
    assert result["rotation"] == 1
    assert result["image"].shape[1] > result["image"].shape[0]


@pytest.mark.parametrize("background,cover", [((230, 230, 230), (30, 30, 30)),
                                            ((110, 110, 110), (120, 120, 120)),
                                            ((255, 0, 0), (0, 0, 97))])
def test_border_recognition_dark_low_contrast_and_same_luminance(background, cover):
    frame = np.full((600, 800, 3), background, np.uint8)
    expected = np.array([[250, 60], [570, 90], [610, 520], [210, 500]], np.int32)
    cv2.fillConvexPoly(frame, expected, cover)
    points, found = detect_corners(frame)
    assert found
    assert np.linalg.norm(points - expected, axis=1).max() < 15


def test_border_detector_does_not_choose_large_frame_over_book():
    frame = np.full((600, 800, 3), 40, np.uint8)
    cv2.rectangle(frame, (8, 8), (791, 591), (160, 160, 160), -1)
    expected = np.array([[250, 60], [570, 90], [610, 520], [210, 500]], np.int32)
    cv2.fillConvexPoly(frame, expected, (230, 230, 230))
    points, found = detect_corners(frame)
    assert found
    assert np.linalg.norm(points - expected, axis=1).max() < 15
