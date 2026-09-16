import os
import time
from google import genai
from google.genai import types

# ==========================================
# 1. 설정 (API 키)
# ==========================================
# 터미널에서 아래와 같이 환경 변수를 설정해야 합니다.
# export GEMINI_API_KEY="여러분의_구글_API_키"

# AI Studio (Gemini API) 클라이언트 초기화
# 자동으로 GEMINI_API_KEY 환경 변수를 로드하여 인증합니다.
client = genai.Client()

# 사용할 Veo 모델 지정 (영상 연장(Extension)을 지원하는 최신 모델 사용)
VEO_MODEL_ID = "veo-3.1-generate-preview" 

# ==========================================
# 2. 영상 생성 시나리오 정의
# ==========================================
# 이미지 파일명과 매칭되는 프롬프트를 리스트로 구성합니다.
# initial_prompt: 처음 5~8초를 생성할 때의 프롬프트
# extensions: 영상을 더 길게 만들기 위해 이어붙일 프롬프트 목록 (각 7초씩 연장됨)
SCENES = [
    {
        "image": "login.png",
        "initial_prompt": "Cinematic tech demo start, sleek dark mode login screen titled 'Protein Pipeline'. Professional laboratory software aesthetic, 4k.",
        "extensions": [
            "The screen transitions smoothly to a futuristic dashboard introducing AI-Driven Autonomous Design.",
            "Text overlay appears: 'Welcome to the future of protein engineering, the intelligent autonomous pipeline.'"
        ]
    },
    {
        "image": "copilot.png",
        "initial_prompt": "A side drawer labeled 'Context Copilot' slides out. AI text appears typing: 'Designing binder...'.",
        "extensions": [
            "The Orchestration Agent analyzes user intent and target PDB, dynamically adjusting temperature and cutoff thresholds.",
            "A visual representation of Multi-Round Iterative Design forms, showing continuous feedback loops."
        ]
    },
    {
        "image": "studio.png",
        "initial_prompt": "Workflow studio screen showing stages: MSA, RFD3, BioEmu, Design, SoluProt, and AF2.",
        "extensions": [
            "Progress bars fill up, visualizing the autonomous orchestration from evolutionary conservation to 3D structure evaluation.",
            "The Iterative Funnel activates: Evaluation Agent analyzes pLDDT and RMSD, while Prompting Agent remasks flexible regions."
        ]
    },
    {
        "image": "monitor.png",
        "initial_prompt": "A monitoring dashboard displays live metrics of distributed GPU and lightweight computations.",
        "extensions": [
            "Data flow lines pulse, showing the autonomous feedback loop maximizing success probability without human intervention.",
            "The dashboard highlights the modular allocation of heavy and light tasks across the system."
        ]
    },
    {
        "image": "analyze.png",
        "initial_prompt": "3D protein structure comparison. Two molecular ribbons align and rotate smoothly.",
        "extensions": [
            "PyMOL-integrated analysis module precisely compares 3D structures, identifying the Top-K Hit List with 1-5% novelty.",
            "The Active Learning engine visualizes autonomous mathematical optimization of complex non-linear mutation synergies (Epistasis)."
        ]
    },
    {
        "image": "residue_picker.png",
        "initial_prompt": "A detailed 3D interface for selecting specific amino acid residues on a protein structure.",
        "extensions": [
            "The interface seamlessly connects to external tools, demonstrating MCP-based ecosystem integration.",
            "A seamless transition from the web browser to an IDE like VS Code, highlighting the unified research environment."
        ]
    }
]

def generate_and_extend_video(image_path, scene_config, output_filename):
    """
    초기 영상을 생성한 후, 여러 번 연장(Extend)하여 긴 영상을 만드는 함수
    """
    print(f"🎬 생성 시작: {image_path} -> {output_filename}")
    
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    # 1. 초기 영상 생성 (약 5~8초)
    print(f"  [1/초기 생성] 프롬프트: {scene_config['initial_prompt'][:50]}...")
    try:
        operation = client.models.generate_videos(
            model=VEO_MODEL_ID,
            prompt=scene_config['initial_prompt'],
            image=types.Image(
                image_bytes=image_bytes,
                mime_type="image/png"
            )
        )
        
        while not operation.done:
            print("    대기 중... (10초 후 재확인)")
            time.sleep(10)
            # google-genai SDK에서는 인스턴스 객체 자체를 넘겨야 합니다.
            operation = client.operations.get(operation)

        if not operation.response:
            print(f"❌ 초기 영상 생성 실패: {operation.error}")
            return None
            
        current_video = operation.response.generated_videos[0]
        print(f"  ✅ 초기 영상 완료 (URI: {current_video.video.uri})")

        # 2. 영상 연장 (Extension) - 각 확장마다 약 7초 추가
        for ext_idx, ext_prompt in enumerate(scene_config.get("extensions", [])):
            print(f"  [{ext_idx+2}/연장] 프롬프트: {ext_prompt[:50]}...")
            
            ext_op = client.models.generate_videos(
                model=VEO_MODEL_ID,
                prompt=ext_prompt,
                video=current_video.video
            )
            
            while not ext_op.done:
                print("    대기 중... (10초 후 재확인)")
                time.sleep(10)
                ext_op = client.operations.get(ext_op)
                
            if not ext_op.response:
                print(f"❌ 영상 연장 실패: {ext_op.error}")
                break # 실패 시 가장 최근에 성공한 비디오를 저장
                
            current_video = ext_op.response.generated_videos[0]
            print(f"  ✅ 연장 완료 (누적 길이 증가)")

        # 3. 최종 결과물 다운로드
        print(f"⬇️ {output_filename} 파일로 최종 결과물 다운로드 중...")
        video_bytes = client.files.download(file=current_video.video)
        with open(output_filename, "wb") as f:
            f.write(video_bytes)
        print("다운로드 완료!")
        
        return output_filename
            
    except Exception as e:
        print(f"API 호출 중 오류 발생: {e}")
        return None

# ==========================================
# 3. 전체 파이프라인 실행
# ==========================================
def main():
    if not os.environ.get("GEMINI_API_KEY"):
        print("⚠️ 에러: GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
        return

    image_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../docs/assets/user-manual"))
    video_results = []

    for i, scene in enumerate(SCENES):
        img_path = os.path.join(image_dir, scene["image"])
        if not os.path.exists(img_path):
            print(f"⚠️ 이미지를 찾을 수 없음 (건너뜀): {img_path}")
            continue
            
        result_path = generate_and_extend_video(img_path, scene, f"scene_{i}_extended.mp4")
        if result_path:
            video_results.append(result_path)

    print("\n🚀 모든 장면 처리가 완료되었습니다.")
    if video_results:
        print("생성된 최종 로컬 영상 목록:", video_results)
        print("💡 참고: 각 영상은 초기 5~8초 + (연장 횟수 * 7초)의 길이를 가집니다.")

if __name__ == "__main__":
    main()