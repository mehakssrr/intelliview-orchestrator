import pytest

from retrieval.index import build_index, retrieve


@pytest.fixture
def sample_documents():
    return [
        "Python is a programming language.",
        "Machine Learning uses data.",
        "Transformers are deep learning models.",
        "Football is a sport.",
    ]


def test_build_and_retrieve(sample_documents):
    # Build the index
    build_index(sample_documents)

    # Retrieve
    results = retrieve("Explain Machine Learning", top_k=1)

    # Assert
    assert len(results) == 1
    assert "Machine Learning" in results[0]


def test_retrieve_without_build_raises_error():
    import retrieval.index

    retrieval.index.index = None  # Reset index

    with pytest.raises(ValueError, match="Index has not been built"):
        retrieve("Test query")
