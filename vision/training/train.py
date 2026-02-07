"""
YOLO 커스텀 모델 학습 스크립트

사용법:
    python vision/training/train.py
    python vision/training/train.py --epochs 100 --batch 16 --imgsz 640
    python vision/training/train.py --resume  (이전 학습 이어하기)
"""

import os
import sys
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_YAML = os.path.join(PROJECT_DIR, "dataset", "aion2.yaml")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "runs")


def check_dataset():
    """학습 전 데이터셋 검증"""
    dataset_dir = os.path.join(PROJECT_DIR, "dataset")

    for split in ["train", "val"]:
        img_dir = os.path.join(dataset_dir, "images", split)
        lbl_dir = os.path.join(dataset_dir, "labels", split)

        if not os.path.exists(img_dir):
            print(f"[오류] 이미지 폴더 없음: {img_dir}")
            return False

        images = [f for f in os.listdir(img_dir)
                  if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        labels = [f for f in os.listdir(lbl_dir)
                  if f.endswith('.txt')] if os.path.exists(lbl_dir) else []

        print(f"  {split}: 이미지 {len(images)}장, 라벨 {len(labels)}개")

        if len(images) == 0:
            print(f"[오류] {split} 이미지가 없습니다.")
            return False

        if len(labels) == 0:
            print(f"[오류] {split} 라벨이 없습니다. 라벨링을 먼저 수행하세요.")
            print(f"  가이드: python vision/training/prepare_dataset.py --guide")
            return False

        # 라벨 없는 이미지 확인
        unlabeled = []
        for img in images:
            lbl_name = os.path.splitext(img)[0] + ".txt"
            if not os.path.exists(os.path.join(lbl_dir, lbl_name)):
                unlabeled.append(img)

        if unlabeled:
            print(f"  경고: 라벨 없는 이미지 {len(unlabeled)}장 (배경으로 처리됨)")

    return True


def train(epochs: int, batch: int, imgsz: int, device: str, resume: bool, base_model: str):
    """YOLO 학습 실행"""
    from ultralytics import YOLO

    print("=" * 50)
    print("  AION2 YOLO 커스텀 모델 학습")
    print("=" * 50)
    print(f"  베이스 모델: {base_model}")
    print(f"  데이터셋: {DATASET_YAML}")
    print(f"  에포크: {epochs}")
    print(f"  배치: {batch}")
    print(f"  이미지 크기: {imgsz}")
    print(f"  디바이스: {device}")
    print(f"  출력: {OUTPUT_DIR}")
    print("=" * 50)
    print()

    # 데이터셋 검증
    print("[검증] 데이터셋 확인...")
    if not check_dataset():
        print("\n학습을 중단합니다.")
        return

    print()

    # 모델 로드
    if resume:
        last_pt = os.path.join(OUTPUT_DIR, "detect", "aion2", "weights", "last.pt")
        if os.path.exists(last_pt):
            print(f"[학습] 이전 학습 이어하기: {last_pt}")
            model = YOLO(last_pt)
        else:
            print(f"[오류] 이전 학습 파일 없음: {last_pt}")
            print("[학습] 새로 시작합니다.")
            model = YOLO(base_model)
    else:
        model = YOLO(base_model)

    # 학습 실행
    results = model.train(
        data=DATASET_YAML,
        epochs=epochs,
        batch=batch,
        imgsz=imgsz,
        device=device if device != "auto" else None,
        project=os.path.join(OUTPUT_DIR, "detect"),
        name="aion2",
        exist_ok=True,
        patience=20,       # 20 에포크 개선 없으면 조기 종료
        save=True,
        save_period=10,     # 10 에포크마다 체크포인트 저장
        plots=True,
        verbose=True,
    )

    # 결과 출력
    best_pt = os.path.join(OUTPUT_DIR, "detect", "aion2", "weights", "best.pt")
    print()
    print("=" * 50)
    print("  학습 완료!")
    print("=" * 50)
    print(f"  최적 모델: {best_pt}")
    print()
    print("  WellcomLAND에서 사용:")
    print(f"    V-Set → 모델 경로: {best_pt}")
    print("    Vision 버튼으로 시작")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="AION2 YOLO 커스텀 모델 학습")
    parser.add_argument("--epochs", type=int, default=50, help="학습 에포크 (기본: 50)")
    parser.add_argument("--batch", type=int, default=8, help="배치 크기 (기본: 8, VRAM에 따라 조정)")
    parser.add_argument("--imgsz", type=int, default=640, help="입력 이미지 크기 (기본: 640)")
    parser.add_argument("--device", default="auto", help="디바이스 (auto/cpu/0/0,1)")
    parser.add_argument("--resume", action="store_true", help="이전 학습 이어하기")
    parser.add_argument("--model", default="yolov8n.pt",
                        help="베이스 모델 (기본: yolov8n.pt, 큰 모델: yolov8s.pt, yolov8m.pt)")
    args = parser.parse_args()

    train(args.epochs, args.batch, args.imgsz, args.device, args.resume, args.model)


if __name__ == "__main__":
    main()
