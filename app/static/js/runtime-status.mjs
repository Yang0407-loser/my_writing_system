export function connectionStateFromStatus(status) {
  return status?.runtime_available === false ? 'reconnecting' : 'online';
}
