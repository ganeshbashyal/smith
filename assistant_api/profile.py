from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class UserProfile:
    interests: tuple[str, ...] = (
        "science and technology",
        "finance and ASX markets",
        "soccer",
        "Formula 1",
        "cars and automotive engineering",
        "Harley-Davidson motorcycles",
        "industrial automation and engineering",
    )
    soccer_teams: tuple[str, ...] = ("Manchester United", "Real Madrid")
    f1_team: str = "Ferrari"
    values: tuple[str, ...] = ("kindness", "fairness")
    background: tuple[str, ...] = (
        "engineer by qualification and trade",
        "more than 15 years in industrial automation and engineering",
        "many years of Australian building-industry experience",
    )

    def as_dict(self) -> dict:
        return asdict(self)


PROFILE = UserProfile()


def morning_snapshot() -> dict:
    return {
        "title": "Good morning — your personal snapshot",
        "profile": PROFILE.as_dict(),
        "sections": [
            {"name": "Science and technology", "status": "ready for live news and research feeds"},
            {"name": "Finance and ASX", "status": "ready for watchlists, prices, announcements, and market news"},
            {"name": "Soccer", "teams": list(PROFILE.soccer_teams), "status": "ready for fixtures, results, tables, and news"},
            {"name": "Formula 1", "teams": [PROFILE.f1_team], "status": "ready for race calendar, results, standings, and news"},
            {"name": "Cars and Harley-Davidson", "status": "ready for automotive and motorcycle news"},
            {"name": "Engineering and automation", "status": "ready for industry, controls, robotics, and manufacturing updates"},
        ],
        "personal_note": "Prioritise useful, evidence-based information with kindness and fairness; be direct when the situation calls for it.",
    }
