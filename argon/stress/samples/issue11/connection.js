const channel = connect();
channel.on('message', onMessage);
channel.on('error', onError);
channel.on('close', onClose);
