"""
데이터셋 준비 도구
raw/ 폴더의 이미지를 train/val로 분할
라벨링 도구 안내 출력

사용법:
    python vision/training/prepare_dataset.py --split 0.8
"""

import os
import sys
import shutil
import random
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(PROJECT_DIR, "dataset")


def split_dataset(raw_dir: str, train_ratio: float = 0.8):
    """raw/ 이미지를 train/val로 분할"""
    images = [f for f in os.listdir(raw_dir)
              if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp'))]

    if not images:
        print(f"[준비] raw/ 폴더에 이미지가 없습니다: {raw_dir}")
        return

    random.shuffle(images)
    split_idx = int(len(images) * train_ratio)
    train_images = images[:split_idx]
    val_images = images[split_idx:]

    train_dir = os.path.join(DATASET_DIR, "images", "train")
    val_dir = os.path.join(DATASET_DIR, "images", "val")
    os.makedirs(train_dir, exist_ok=True)
    os.makedirs(val_dir, exist_ok=True)

    for img in train_images:
        shutil.copy2(os.path.join(raw_dir, img), os.path.join(train_dir, img))
    for img in val_images:
        shutil.copy2(os.path.join(raw_dir, img), os.path.join(val_dir, img))

    print(f"[준비] 분할 완료:")
    print(f"  전체: {len(images)}장")
    print(f"  train: {len(train_images)}장 → {train_dir}")
    print(f"  val:   {len(val_images)}장 → {val_dir}")


def check_labels():
    """라벨 파일 존재 여부 확인"""
    for split in ["train", "val"]:
        img_dir = os.path.join(DATASET_DIR, "images", split)
        lbl_dir = os.path.join(DATASET_DIR, "labels", split)

        if not os.path.exists(img_dir):
            continue

        images = [f for f in os.listdir(img_dir)
                  if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp'))]
        labeled = 0
        for img in images:
            lbl_name = os.path.splitext(img)[0] + ".txt"
            if os.path.exists(os.path.join(lbl_dir, lbl_name)):
                labeled += 1

        print(f"  {split}: {labeled}/{len(images)}장 라벨링됨")

    if labeled < len(images):
        print()
        print_labeling_guide()


def print_labeling_guide():
    """라벨링 도구 안내"""
    print("=" * 60)
    print("  라벨링 가이드")
    print("=" * 60)
    print()
    print("  YOLO 라벨링 도구를 사용하여 이미지에 바운딩 박스를 그리세요.")
    print()
    print("  추천 도구:")
    print("    1. labelImg (로컬, 가벼움)")
    print("       pip install labelImg")
    print("       labelImg dataset/images/train")
    print("       -> 저장 형식을 'YOLO'로 변경 필수!")
    print()
    print("    2. CVAT (웹 기반, 팀 협업)")
    print("       https://www.cvat.ai/")
    print()
    print("    3. Roboflow (웹 기반, 간편)")
    print("       https://roboflow.com/")
    print()
    print("  YOLO 라벨 형식 (각 이미지마다 .txt 파일):")
    print("    <class_id> <center_x> <center_y> <width> <height>")
    print("    좌표는 0~1로 정규화 (이미지 크기 대비 비율)")
    print()
    print("  예시 (frame_001.txt):")
    print("    0 0.45 0.30 0.10 0.15    # monster at center-left")
    print("    5 0.50 0.95 0.30 0.03    # hp_bar at bottom-center")
    print("    12 0.60 0.50 0.04 0.04   # drop_item")
    print()
    print("  클래스 ID (dataset/aion2.yaml 참조):")
    print("    0: monster, 1: elite_monster, 2: boss, 3: npc, 4: player")
    print("    5: hp_bar, 6: mp_bar, 7: skill_icon, 8: minimap, 9: quest_marker")
    print("    10: chat_window, 11: exp_bar, 12: drop_item, 13: loot_bag, 14: treasure_chest")
    print()
    print("  라벨 파일 저장 위치:")
    print(f"    train: {os.path.join(DATASET_DIR, 'labels', 'train')}")
    print(f"    val:   {os.path.join(DATASET_DIR, 'labels', 'val')}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="데이터셋 준비 (분할 + 라벨 확인)")
    parser.add_argument("--split", type=float, default=0.8, help="train 비율 (기본: 0.8)")
    parser.add_argument("--raw", default=os.path.join(DATASET_DIR, "images", "raw"),
                        help="raw 이미지 경로")
    parser.add_argument("--guide", action="store_true", help="라벨링 가이드만 출력")
    args = parser.parse_args()

    if args.guide:
        print_labeling_guide()
        return

    print("[준비] 데이터셋 분할")
    split_dataset(args.raw, args.split)
    print()
    print("[준비] 라벨 상태 확인")
    check_labels()


if __name__ == "__main__":
    main()
