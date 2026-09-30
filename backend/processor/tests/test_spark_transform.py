import pytest

from processor.tasks import apply_operation


@pytest.fixture(scope="session")
def spark():
    pytest.importorskip("pyspark")
    from pyspark.sql import SparkSession

    session = SparkSession.builder.master("local[1]").appName("regex-processor-tests").getOrCreate()
    yield session
    session.stop()


def test_replace_and_normalize_whitespace_are_spark_transformations(spark):
    input_frame = spark.createDataFrame(
        [("john.doe@example.com", "  Jane    Doe  ")], ["email", "name"]
    )
    masked = apply_operation(
        input_frame,
        ["email"],
        "replace",
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "REDACTED",
    )
    result = apply_operation(masked, ["name"], "normalize_whitespace", None, "").first().asDict()
    assert result == {"email": "REDACTED", "name": "Jane Doe"}
