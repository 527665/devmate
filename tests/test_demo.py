from app.demo import add


def test_add_positive_numbers():
    # Arrange
    a, b = 2, 3
    # Act
    result = add(a, b)
    # Assert
    assert result == 5


def test_add_with_zero():
    assert add(0, 0) == 0


def test_add_negative_numbers():
    assert add(-1, -1) == -2
