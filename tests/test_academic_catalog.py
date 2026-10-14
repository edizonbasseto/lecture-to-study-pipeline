from datetime import date
from pathlib import Path

import pytest
import yaml

from lecture_study.study_materials.academic_catalog import (
    ACADEMIC_BUNDLE_DOCUMENT_FILES,
    BUNDLE_FIGURES_SUBDIRECTORY,
    AcademicBundleIdentityManifest,
    AcademicCatalog,
    AcademicScheduleEntry,
    academic_bundle_is_complete,
    academic_lecture_relative_path,
    academic_lecture_paths,
    academic_period_for,
    load_academic_catalog,
    render_academic_catalog,
)


def _catalog_payload() -> dict:
    return {
        "disciplines": [
            {
                "title": "Disciplina A",
                "url": "https://example.test/course/view.php?id=1",
                "program": "Pós-graduação",
                "lessons": [
                    {
                        "activity_id": "10001",
                        "moodle_order": 1,
                        "section": "Tópico 1",
                        "display_title": "Semiologia oftálmica",
                        "url": "https://example.test/mod/supervideo/view.php?id=10001",
                        "enabled": True,
                        "discovery_timestamp": "2026-01-01T00:00:00+00:00",
                    },
                    {
                        "activity_id": "10011",
                        "moodle_order": 2,
                        "section": "Cronograma",
                        "display_title": "Cronograma",
                        "url": "https://example.test/mod/resource/view.php?id=10011",
                        "enabled": True,
                        "discovery_timestamp": "2026-01-01T00:00:00+00:00",
                    },
                ],
            },
            {
                "title": "Disciplina B",
                "url": "https://example.test/course/view.php?id=2",
                "program": "Pós-graduação",
                "lessons": [
                    {
                        "activity_id": "10003",
                        "moodle_order": 1,
                        "section": "Tópico 2",
                        "display_title": "Cirurgias do baço",
                        "url": "https://example.test/mod/supervideo/view.php?id=10003",
                        "enabled": True,
                        "discovery_timestamp": "2026-01-01T00:00:00+00:00",
                    }
                ],
            },
        ]
    }


def _catalog() -> AcademicCatalog:
    return AcademicCatalog.model_validate(_catalog_payload())


def test_academic_period_uses_calendar_semesters() -> None:
    assert academic_period_for(date(2026, 6, 30)) == "2026.1"
    assert academic_period_for(date(2026, 7, 1)) == "2026.2"


def test_academic_lecture_path_mirrors_catalog_hierarchy() -> None:
    schedule = {
        "10001": AcademicScheduleEntry(
            lecture_id="10001",
            scheduled_date=date(2026, 3, 10),
            schedule_topic="Oftalmo 1",
        )
    }

    relative_path = academic_lecture_relative_path(
        _catalog(),
        schedule,
        "10001",
    )

    assert relative_path == Path(
        "2026/1-semestre/disciplina-a/topico-1/10001-semiologia-oftalmica"
    )


def test_academic_paths_validate_the_complete_video_layout() -> None:
    paths = academic_lecture_paths(
        _catalog(),
        {
            "10001": AcademicScheduleEntry(
                lecture_id="10001",
                scheduled_date=date(2026, 3, 10),
                schedule_topic="Oftalmo 1",
            ),
            "10003": AcademicScheduleEntry(
                lecture_id="10003",
                scheduled_date=date(2026, 8, 12),
                schedule_topic="Cirurgias do baço",
            ),
        },
    )

    assert set(paths) == {"10001", "10003"}
    assert len(set(paths.values())) == len(paths)


def test_academic_paths_validate_only_selected_lectures() -> None:
    paths = academic_lecture_paths(
        _catalog(),
        {
            "10001": AcademicScheduleEntry(
                lecture_id="10001",
                scheduled_date=date(2026, 3, 10),
                schedule_topic="Oftalmo 1",
            )
        },
        lecture_ids={"10001"},
    )

    assert set(paths) == {"10001"}


@pytest.mark.parametrize(
    "payload",
    [
        {"disciplines": "inválido"},
        {
            "disciplines": [
                {
                    **_catalog_payload()["disciplines"][0],
                    "lessons": [
                        {
                            **_catalog_payload()["disciplines"][0]["lessons"][0],
                            "moodle_order": "1",
                        }
                    ],
                }
            ]
        },
        {
            "disciplines": [
                {
                    **_catalog_payload()["disciplines"][0],
                    "lessons": [
                        _catalog_payload()["disciplines"][0]["lessons"][0],
                        _catalog_payload()["disciplines"][0]["lessons"][0],
                    ],
                }
            ]
        },
    ],
)
def test_academic_catalog_loader_rejects_invalid_or_ambiguous_data(
    tmp_path: Path,
    payload: dict,
) -> None:
    catalog_path = tmp_path / "targets.local.yaml"
    catalog_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="catálogo acadêmico inválido"):
        load_academic_catalog(catalog_path)


def test_academic_catalog_loader_accepts_blank_optional_program(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload()
    payload["disciplines"][0]["program"] = ""
    catalog_path = tmp_path / "targets.local.yaml"
    catalog_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True),
        encoding="utf-8",
    )

    catalog = load_academic_catalog(catalog_path)

    assert catalog.disciplines[0].program == ""


def test_catalog_groups_video_lessons_and_marks_existing_guides(tmp_path: Path) -> None:
    completed_index = (
        tmp_path
        / "2026/1-semestre/disciplina-a/topico-1"
        / "10001-semiologia-oftalmica"
        / "indice.md"
    )
    completed_index.parent.mkdir(parents=True)
    completed_index.write_text(
        "# Aula\n\nAula `10001` - pacote identificado.\n",
        encoding="utf-8",
    )
    (completed_index.parent / ".bundle.json").write_text(
        AcademicBundleIdentityManifest(
            schema_version=1,
            lecture_id="10001",
        ).model_dump_json(),
        encoding="utf-8",
    )
    schedule = {
        "10001": AcademicScheduleEntry(
            lecture_id="10001",
            scheduled_date=date(2026, 3, 10),
            schedule_topic="Oftalmo 1",
        ),
        "10003": AcademicScheduleEntry(
            lecture_id="10003",
            scheduled_date=date(2026, 8, 12),
            schedule_topic="Cirurgias do baço",
        ),
    }

    rendered = render_academic_catalog(_catalog(), schedule, tmp_path)

    assert "## 1º semestre de 2026" in rendered
    assert "## 2º semestre de 2026" in rendered
    assert (
        "[Semiologia oftálmica]"
        "(2026/1-semestre/disciplina-a/topico-1/"
        "10001-semiologia-oftalmica/indice.md)"
    ) in rendered
    assert "Concluída" in rendered
    assert "Cirurgias do baço" in rendered
    assert "Pendente" in rendered
    assert "Cronograma" not in rendered


def test_bundle_completion_accepts_matching_legacy_index(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "10001-aula"
    _write_legacy_bundle_shape(bundle_dir, "10001")

    assert academic_bundle_is_complete(bundle_dir, "10001")


def test_bundle_completion_rejects_wrong_manifest_identity(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "10001-aula"
    bundle_dir.mkdir()
    (bundle_dir / "indice.md").write_text(
        "# Aula\n\nAula `10003` - outro pacote.\n",
        encoding="utf-8",
    )
    (bundle_dir / ".bundle.json").write_text(
        AcademicBundleIdentityManifest(
            schema_version=1,
            lecture_id="10003",
        ).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="pertence à aula 10003"):
        academic_bundle_is_complete(bundle_dir, "10001")


def test_bundle_completion_rejects_identity_free_index(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "10001-aula"
    bundle_dir.mkdir()
    (bundle_dir / "indice.md").write_text("# Aula\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sem identidade"):
        academic_bundle_is_complete(bundle_dir, "10001")


def test_bundle_completion_rejects_malformed_manifest(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "10001-aula"
    bundle_dir.mkdir()
    (bundle_dir / "indice.md").write_text(
        "# Aula\n\nAula `10001` - pacote.\n",
        encoding="utf-8",
    )
    (bundle_dir / ".bundle.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        academic_bundle_is_complete(bundle_dir, "10001")


def test_bundle_completion_rejects_symlinked_index(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "10001-aula"
    bundle_dir.mkdir()
    external_index = tmp_path / "external-index.md"
    external_index.write_text(
        "# Aula\n\nAula `10001` - pacote.\n",
        encoding="utf-8",
    )
    (bundle_dir / "indice.md").symlink_to(external_index)

    with pytest.raises(ValueError, match="link simbólico"):
        academic_bundle_is_complete(bundle_dir, "10001")


def _write_legacy_bundle_shape(bundle_dir: Path, lecture_id: str) -> None:
    for relative_path in ACADEMIC_BUNDLE_DOCUMENT_FILES:
        file_path = bundle_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"# Aula\n\nAula `{lecture_id}` - pacote legado.\n"
            if relative_path == "indice.md"
            else "artefato legado"
        )
        file_path.write_text(content, encoding="utf-8")
    video_path = bundle_dir / "video/aula.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video-content")


def test_catalog_rejects_video_without_official_schedule_date(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="10003"):
        render_academic_catalog(
            _catalog(),
            {
                "10001": AcademicScheduleEntry(
                    lecture_id="10001",
                    scheduled_date=date(2026, 3, 10),
                    schedule_topic="Oftalmo 1",
                )
            },
            tmp_path,
        )


def test_catalog_can_list_unscheduled_videos_without_assigning_a_semester(
    tmp_path: Path,
) -> None:
    rendered = render_academic_catalog(
        _catalog(),
        {
            "10001": AcademicScheduleEntry(
                lecture_id="10001",
                scheduled_date=date(2026, 3, 10),
                schedule_topic="Oftalmo 1",
            )
        },
        tmp_path,
        include_unscheduled=True,
    )

    assert "## Sem data oficial" in rendered
    assert "Cirurgias do baço (`10003`) - **Aguardando data oficial**" in rendered
    unscheduled_section = rendered.split("## Sem data oficial", maxsplit=1)[1]
    assert "semestre" in unscheduled_section
    assert "12/08/2026" not in unscheduled_section


def test_catalog_orders_lessons_by_official_date_within_section(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload()
    lessons = payload["disciplines"][0]["lessons"]
    lessons.insert(
        1,
        {
            **lessons[0],
            "activity_id": "10017",
            "display_title": "Proptose bulbar",
        },
    )
    schedule = {
        "10001": AcademicScheduleEntry(
            lecture_id="10001",
            scheduled_date=date(2026, 3, 24),
            schedule_topic="Oftalmo 3",
        ),
        "10017": AcademicScheduleEntry(
            lecture_id="10017",
            scheduled_date=date(2026, 3, 17),
            schedule_topic="Oftalmo 2",
        ),
        "10003": AcademicScheduleEntry(
            lecture_id="10003",
            scheduled_date=date(2026, 8, 12),
            schedule_topic="Cirurgias do baço",
        ),
    }

    rendered = render_academic_catalog(
        AcademicCatalog.model_validate(payload),
        schedule,
        tmp_path,
    )

    assert rendered.index("Proptose bulbar") < rendered.index("Semiologia oftálmica")


def test_legacy_bundle_document_set_carries_no_figure_filename() -> None:
    assert not any("figura" in filename for filename in ACADEMIC_BUNDLE_DOCUMENT_FILES)
    assert BUNDLE_FIGURES_SUBDIRECTORY not in ACADEMIC_BUNDLE_DOCUMENT_FILES


def test_legacy_shape_validator_still_accepts_a_bundle_without_figures(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "10001-aula"
    _write_legacy_bundle_shape(bundle_dir, "10001")

    assert academic_bundle_is_complete(bundle_dir, "10001")


_TARGETS_EXAMPLE_PATH = Path(__file__).parents[1] / "config" / "targets.example.yaml"


def test_committed_targets_example_is_accepted_by_the_catalog_reader() -> None:
    catalog = load_academic_catalog(_TARGETS_EXAMPLE_PATH)

    lessons = [
        lesson for discipline in catalog.disciplines for lesson in discipline.lessons
    ]
    assert catalog.disciplines[0].title == "Disciplina A"
    assert all(int(lesson.activity_id) >= 10001 for lesson in lessons)
    assert any(lesson.is_video for lesson in lessons)
    assert any(not lesson.is_video for lesson in lessons)
