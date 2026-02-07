"""
반자동 라벨링 도구
yolov8n 기본 모델로 1차 자동 감지 → YOLO 라벨 파일 생성
이후 labelImg에서 수정/보완만 하면 됨

사용법:
    python vision/training/auto_label.py
    python vision/training/auto_label.py --conf 0.3 --split train
    python vision/training/auto_label.py --split val

COCO 클래스 → 아이온2 클래스 매핑:
    yolov8n의 person(0) → player(4)로 매핑
    나머지는 커스텀 클래스이므로 수동 라벨링 필요
"""

import os
import sys
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(PROJECT_DIR, "dataset")

# COCO 클래스 → 아이온2 클래스 매핑
# yolov8n은 COCO 80 클래스를 감지함
# 게임에서 사람 형태 객체는 player/npc/monster 후보가 됨
COCO_TO_AION2 = {
    0: 4,    # person → player (수동으로 monster/npc로 재분류 필요)
}


def auto_label(split: str, conf: float, model_path: str):
    """지정 split의 이미지를 yolov8n으로 자동 라벨링"""
    from ultralytics import YOLO
    import torch

    img_dir = os.path.join(DATASET_DIR, "images", split)
    lbl_dir = os.path.join(DATASET_DIR, "labels", split)
    os.makedirs(lbl_dir, exist_ok=True)

    if not os.path.exists(img_dir):
        print(f"[오류] 이미지 폴더 없음: {img_dir}")
        return

    images = sorted([f for f in os.listdir(img_dir)
                     if f.lower().endswith(('.jpg', '.jpeg', '.png'))])

    if not images:
        print(f"[오류] {split}에 이미지가 없습니다.")
        return

    # 이미 라벨이 있는 파일 건너뛰기
    existing = set()
    if os.path.exists(lbl_dir):
        existing = {os.path.splitext(f)[0] for f in os.listdir(lbl_dir) if f.endswith('.txt')}

    to_label = [f for f in images if os.path.splitext(f)[0] not in existing]

    print(f"[자동 라벨링] {split}")
    print(f"  전체 이미지: {len(images)}장")
    print(f"  기존 라벨: {len(existing)}개")
    print(f"  라벨링 대상: {len(to_label)}장")
    print(f"  모델: {model_path}")
    print(f"  신뢰도: {conf}")
    print()

    if not to_label:
        print("  라벨링할 이미지가 없습니다.")
        return

    # 모델 로드
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  디바이스: {device}")
    model = YOLO(model_path)

    labeled = 0
    total_boxes = 0

    for i, img_name in enumerate(to_label):
        img_path = os.path.join(img_dir, img_name)
        lbl_name = os.path.splitext(img_name)[0] + ".txt"
        lbl_path = os.path.join(lbl_dir, lbl_name)

        try:
            results = model.predict(img_path, conf=conf, device=device, verbose=False)

            if not results or len(results) == 0:
                # 빈 라벨 파일 생성 (배경 이미지)
                with open(lbl_path, 'w') as f:
                    pass
                labeled += 1
                continue

            result = results[0]
            img_h, img_w = result.orig_shape
            lines = []

            if result.boxes is not None and len(result.boxes) > 0:
                for box in result.boxes:
                    coco_cls = int(box.cls[0])

                    # COCO → 아이온2 매핑
                    if coco_cls not in COCO_TO_AION2:
                        continue  # 매핑 안 되는 COCO 클래스는 무시

                    aion2_cls = COCO_TO_AION2[coco_cls]
                    x1, y1, x2, y2 = box.xyxy[0].tolist()

                    # YOLO 형식으로 변환 (center_x, center_y, width, height - 정규화)
                    cx = ((x1 + x2) / 2) / img_w
                    cy = ((y1 + y2) / 2) / img_h
                    w = (x2 - x1) / img_w
                    h = (y2 - y1) / img_h

                    lines.append(f"{aion2_cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

            with open(lbl_path, 'w') as f:
                f.write('\n'.join(lines))

            total_boxes += len(lines)
            labeled += 1

            if (i + 1) % 50 == 0 or (i + 1) == len(to_label):
                print(f"  진행: {i + 1}/{len(to_label)} ({total_boxes}개 박스)")

        except Exception as e:
            print(f"  오류 [{img_name}]: {e}")

    print(f"\n[자동 라벨링 완료]")
    print(f"  라벨링: {labeled}장")
    print(f"  총 박스: {total_boxes}개")
    print(f"  저장: {lbl_dir}")
    print()
    print(f"[다음 단계]")
    print(f"  자동 라벨은 person→player 매핑만 수행합니다.")
    print(f"  labelImg로 열어서 다음을 수정/추가하세요:")
    print(f"    - player를 monster/npc로 재분류")
    print(f"    - hp_bar, mp_bar, skill_icon 등 UI 요소 추가")
    print(f"    - drop_item, loot_bag 등 아이템 추가")
    print(f"    - 잘못된 박스 삭제")
    print()
    print(f"  labelImg 실행:")
    print(f"    labelImg {img_dir}")


def main():
    parser = argparse.ArgumentParser(description="반자동 라벨링 (yolov8n → YOLO 라벨)")
    parser.add_argument("--split", default="train", choices=["train", "val", "both"],
                        help="라벨링할 분할 (기본: train)")
    parser.add_argument("--conf", type=float, default=0.25,
                        help="감지 신뢰도 (기본: 0.25, 낮을수록 더 많이 감지)")
    parser.add_argument("--model", default="yolov8n.pt", help="베이스 모델")
    args = parser.parse_args()

    splits = ["train", "val"] if args.split == "both" else [args.split]
    for split in splits:
        auto_label(split, args.conf, args.model)


if __name__ == "__main__":
    main()
