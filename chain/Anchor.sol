// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Anchor {
    address public immutable owner;

    event Anchored(bytes32 indexed root, uint64 firstSeq, uint64 lastSeq, bytes32 head);

    modifier onlyOwner() {
        require(msg.sender == owner, "Anchor: caller is not owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    function anchor(bytes32 root, uint64 firstSeq, uint64 lastSeq, bytes32 head) external onlyOwner {
        emit Anchored(root, firstSeq, lastSeq, head);
    }
}
