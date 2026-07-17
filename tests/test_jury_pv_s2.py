"""Regroupement des envois S2 par UE dans les PV de jury."""

from mne_grade_manager.services.jury_reports import _group_second_session_by_course


class _FakeRepo:
    def list_template_blocks_with_courses(self, template_id: int):
        return [
            (
                "Bloc 1",
                [
                    {"course_id": 10, "code": "S1-C-MATH", "name": "Mathematics"},
                    {"course_id": 20, "code": "S1-C-NEUT", "name": "Neutron physics"},
                ],
            )
        ]


def test_group_second_session_by_course_orders_by_template_and_name() -> None:
    rows = [
        {
            "course_id": 20,
            "course_code": "S1-C-NEUT",
            "course_name": "Neutron physics",
            "st_last": "Amoyal",
            "st_first": "Louis",
        },
        {
            "course_id": 10,
            "course_code": "S1-C-MATH",
            "course_name": "Mathematics",
            "st_last": "Benaziz",
            "st_first": "Younes",
        },
        {
            "course_id": 10,
            "course_code": "S1-C-MATH",
            "course_name": "Mathematics",
            "st_last": "Amoyal",
            "st_first": "Louis",
        },
    ]
    grouped = _group_second_session_by_course(_FakeRepo(), 1, rows)
    assert [int(c["course_id"]) for c, _ in grouped] == [10, 20]
    math_students = grouped[0][1]
    assert [s["st_last"] for s in math_students] == ["Amoyal", "Benaziz"]
    assert len(grouped[1][1]) == 1
