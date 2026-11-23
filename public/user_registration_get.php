<?php
require_once __DIR__ . '/db_login.php';
require_form_post();

$username = $_POST['username'] ?? null;
$password = $_POST['password'] ?? null;
if (!is_string($username) || !is_string($password)) {
    $_SESSION['registrationStatus'] = 4;
    redirect_to('user_registration.php');
}
$username = trim($username);
if ($username === '' || mb_strlen($username, 'UTF-8') > 64
    || preg_match('/[\x00-\x1F\x7F]/u', $username)
    || strlen($password) < 8 || strlen($password) > 72 || str_contains($password, "\0")) {
    $_SESSION['registrationStatus'] = 4;
    redirect_to('user_registration.php');
}

$connection = account_database();
try {
    $hash = password_hash($password, PASSWORD_DEFAULT);
    $statement = $connection->prepare('INSERT INTO Account (Username, Password) VALUES (?, ?)');
    $statement->bind_param('ss', $username, $hash);
    $statement->execute();
    $_SESSION['registrationStatus'] = 1;
} catch (mysqli_sql_exception $error) {
    $_SESSION['registrationStatus'] = $error->getCode() === 1062 ? 2 : 3;
    if ($error->getCode() !== 1062) {
        error_log('Account registration failed (' . $error->getCode() . ')');
    }
} finally {
    $connection->close();
}
redirect_to('user_registration.php');
