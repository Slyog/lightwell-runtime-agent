from api_signals import extract_api_signals


def test_socket_gaierror_errno_minus_3_is_network_failure() -> None:
    signals = extract_api_signals("", "socket.gaierror: [Errno -3] Try again")

    assert signals["network_reachable"] is False
    assert signals["is_infrastructure_failure"] is True
    assert signals["failure_category"] == "network"
    assert signals["success"] is False
    assert signals["final_success"] is False


if __name__ == "__main__":
    test_socket_gaierror_errno_minus_3_is_network_failure()
    print("api signal tests passed")
