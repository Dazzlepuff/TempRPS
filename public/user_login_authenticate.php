<?php
require_once __DIR__ . '/db_login.php';
require_form_post();

$username = $_POST['username'] ?? null;
$password = $_POST['password'] ?? null;
if (!is_string($username) || !is_string($password)
    || strlen($password) > 72 || str_contains($password, "\0")) {
    $_SESSION['login failed'] = true;
    redirect_to('user_login.php');
}
$username = trim($username);
$connection = account_database();
$statement = $connection->prepare('SELECT AccountID, Username, Password FROM Account WHERE Username = ? LIMIT 1');
$statement->bind_param('s', $username);
$statement->execute();
$account = $statement->get_result()->fetch_assoc();

if (!$account || !password_verify($password, $account['Password'])) {
    $_SESSION['login failed'] = true;
    $connection->close();
    redirect_to('user_login.php');
}

if (password_needs_rehash($account['Password'], PASSWORD_DEFAULT)) {
    $hash = password_hash($password, PASSWORD_DEFAULT);
    $update = $connection->prepare('UPDATE Account SET Password = ? WHERE AccountID = ?');
    $update->bind_param('si', $hash, $account['AccountID']);
    $update->execute();
}
$connection->close();
session_regenerate_id(true);
$_SESSION = [
    'accountID' => (int) $account['AccountID'],
    'username' => $account['Username'],
    'player_id' => 'account:' . $account['AccountID'],
];
redirect_to('game.php');
