# 🚲 서울 자전거 편의 지도

경사·도로 유형·포장·교통량(차로·제한속도)·터널을 반영해 편한 자전거 경로를 찾아주는 Streamlit 앱.

## 파일 구성
```
app.py            화면 (Streamlit)
engine.py         경로 계산 엔진
requirements.txt  필요한 패키지
data/
  app_graph.npz   도로망 + 점수 (Colab에서 생성)
  app_geom.npz    도로 곡선 좌표
  places.csv      장소 검색용 서울 25개 구 장소
```

## 점수 기준
편안함 점수 = 경사 0.35 + 도로 유형 0.30 + 교통(차로·속도) 0.15 + 포장 0.10 + 통과 불편(터널 등) 0.10

경로 비용 = 예상 시간 × (1 + 4 × 편안함 비중 × 불편도)

- 편안함 비중 0% → 가장 빠른 길
- 편안함 비중 100% → 가장 편한 길

## 배포 (Streamlit Community Cloud, 무료)
1. GitHub에서 새 저장소(Public)를 만들고, 이 폴더의 파일을 **폴더 구조 그대로** 업로드합니다. `data` 폴더도 같이 올립니다.
2. share.streamlit.io에 GitHub 계정으로 로그인합니다.
3. **Create app**을 누르고 저장소를 선택합니다. Main file은 `app.py`로 지정한 뒤 **Deploy**를 누릅니다.

## 선택 설정 (Streamlit 앱 Settings → Secrets)
```
KAKAO_REST_KEY = "카카오 REST API 키"     # 넣으면 장소 검색이 카카오 검색으로 확장됨
FEEDBACK_URL = "구글폼 링크"               # 넣으면 '피드백 남기기' 버튼이 생김
```

## 주의
주행 중 조작 금지. 출발 전 경로 숙지용입니다.
