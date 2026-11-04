"""개발용 샘플 데이터 생성.

    python seed.py          # DB가 비어 있을 때만 생성
    python seed.py --reset  # DB를 지우고 새로 생성
"""
import os
import random
import sys
from datetime import timedelta

from app import app
from models import (Comment, Community, ConItem, ConPack, PointLog, Post, SocialAccount, User,
                    UserConPack, Vote, db, utcnow)

random.seed(42)

COMMUNIS = [
    ("free", "자유", "아무 이야기나 자유롭게"),
    ("game", "게임", "게임 이야기, 공략, 뉴스"),
    ("anime", "애니", "애니메이션 · 만화 · 라노벨"),
    ("stock", "주식", "주식 · 코인 · 재테크"),
    ("soccer", "해외축구", "EPL, 라리가, 챔스"),
    ("cat", "고양이", "냥이 사진 자랑하는 곳"),
    ("dev", "프로그래밍", "개발자들의 쉼터"),
    ("food", "맛집", "오늘 뭐 먹지?"),
]
TITLES = [
    "오늘 진짜 웃긴 일 있었음", "이거 나만 몰랐냐?", "질문 하나만 해도 됨?", "정보) 이번 주 업데이트 정리",
    "후기) 직접 써보고 남김", "와 이건 레전드다", "솔직히 이건 좀 아니지 않냐", "다들 주말에 뭐함?",
    "초보인데 추천 좀 해줘", "ㅋㅋㅋㅋ 이거 봐라", "근데 이거 왜 이럼?", "오랜만에 글 써봄",
    "이번 시즌 예상해본다", "꿀팁 공유한다", "드디어 해냈다!!", "논란 정리해드림",
]
BODIES = [
    "제목 그대로임.\n다들 어떻게 생각함?",
    "방금 겪은 일인데 아직도 어이가 없다 ㅋㅋ\n자세히 쓰면 길어서 요약하자면 그냥 운이 좋았음.",
    "검색해봐도 안 나와서 질문함.\n아는 사람 댓글 좀 부탁해!",
    "정리해봤음.\n1. 첫 번째\n2. 두 번째\n3. 세 번째\n틀린 거 있으면 알려줘.",
]
COMMENTS = ["ㅋㅋㅋㅋㅋ", "ㄹㅇ", "개추", "이건 좀...", "정보 고마워", "나도 궁금했음", "출처는?", "와 대박", "ㅇㅈ", "잘 봤습니다"]
FACES = [("#ffd43b", "^ ^", "M30 62 Q50 80 70 62"), ("#ff8787", "> <", "M32 68 Q50 58 68 68"),
         ("#74c0fc", "T T", "M34 70 Q50 62 66 70"), ("#8ce99a", "o o", "M40 66 Q50 74 60 66"),
         ("#ffa94d", "- -", "M34 66 L66 66"), ("#da77f2", "@ @", "M36 62 Q50 82 64 62")]


def make_seed_cons(owner):
    folder = os.path.join(app.static_folder, "seed", "cons")
    os.makedirs(folder, exist_ok=True)
    pack = ConPack(creator_id=owner.id, name="기본 표정콘", description="커뮤액티브 기본 커뮤콘", price=0)
    db.session.add(pack)
    db.session.flush()
    for i, (color, eyes, mouth) in enumerate(FACES):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
               f'<circle cx="50" cy="50" r="44" fill="{color}" stroke="#333" stroke-width="3"/>'
               f'<text x="50" y="50" font-size="22" text-anchor="middle" font-family="monospace" font-weight="bold" fill="#333">{eyes}</text>'
               f'<path d="{mouth}" stroke="#333" stroke-width="4" fill="none" stroke-linecap="round"/></svg>')
        name = f"face{i}.svg"
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(svg)
        db.session.add(ConItem(pack_id=pack.id, path=f"seed/cons/{name}", sort_order=i))

    paid = ConPack(creator_id=owner.id, name="프리미엄 표정콘", description="포인트로 사는 샘플 콘", price=500)
    db.session.add(paid)
    db.session.flush()
    for i in reversed(range(len(FACES))):
        db.session.add(ConItem(pack_id=paid.id, path=f"seed/cons/face{i}.svg", sort_order=len(FACES) - i))
    return pack


def seed():
    users = []
    for nick in ["관리자", "뉴비", "고인물", "눈팅족", "댓글러", "냥집사", "코딩노예", "축구광"]:
        u = User(nickname=nick, points=random.randint(0, 3000), is_admin=(nick == "관리자"))
        db.session.add(u)
        db.session.flush()
        db.session.add(SocialAccount(user_id=u.id, provider="dev", provider_uid=nick))
        users.append(u)

    pack = make_seed_cons(users[0])
    for u in users:
        db.session.add(UserConPack(user_id=u.id, pack_id=pack.id))
    pack.sales_count = len(users)

    communis = []
    for slug, name, desc in COMMUNIS:
        c = Community(slug=slug, name=name, description=desc, creator_id=users[0].id)
        db.session.add(c)
        communis.append(c)
    db.session.flush()

    now = utcnow()
    con_items = pack.items
    for _ in range(160):
        author = random.choice(users)
        created = now - timedelta(minutes=random.randint(5, 60 * 24 * 9))
        p = Post(community_id=random.choice(communis).id, user_id=author.id,
                 title=random.choice(TITLES), content=random.choice(BODIES),
                 views=random.randint(5, 900), created_at=created, points_awarded=200)
        db.session.add(p)
        db.session.flush()

        voters = random.sample([u for u in users if u.id != author.id], k=random.choice([0, 0, 1, 2, 3, 4, 5, 6, 7]))
        for v in voters:
            value = 1 if random.random() < 0.85 else -1
            vote_time = min(now, created + timedelta(minutes=random.randint(1, 240)))
            db.session.add(Vote(post_id=p.id, user_id=v.id, value=value, created_at=vote_time))
            if value == 1:
                p.upvotes += 1
            else:
                p.downvotes += 1

        for _ in range(random.randint(0, 6)):
            use_con = random.random() < 0.25
            db.session.add(Comment(
                post_id=p.id, user_id=random.choice(users).id,
                content="" if use_con and random.random() < 0.5 else random.choice(COMMENTS),
                con_item_id=random.choice(con_items).id if use_con else None,
                created_at=min(now, created + timedelta(minutes=random.randint(1, 600))),
                points_awarded=50,
            ))
            p.comment_count += 1

    db.session.add(PointLog(user_id=users[0].id, amount=0, kind="seed", reason="샘플 데이터 생성"))
    db.session.commit()
    print(f"샘플 데이터 생성 완료: 유저 {len(users)}명, 커뮤니 {len(communis)}개, 글 160개")
    print("개발용 로그인(DEV_LOGIN=1)에서 '관리자', '뉴비' 등 닉네임으로 로그인할 수 있습니다.")


if __name__ == "__main__":
    with app.app_context():
        if "--reset" in sys.argv:
            db.drop_all()
            db.create_all()
        elif User.query.first():
            print("이미 데이터가 있습니다. 새로 만들려면: python seed.py --reset")
            sys.exit(0)
        seed()
